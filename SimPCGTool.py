import omni.usd
import omni.ui as ui
import omni.kit.app
from pxr import UsdGeom, Gf, Sdf, UsdShade
import random
import math
import asyncio

stage = omni.usd.get_context().get_stage()

PREVIEW_ROOT = "/World/PCG_Preview"
FINAL_ROOT = "/World/PCG_Final"
MATERIAL_ROOT = "/World/PCG_Materials"

pcg_window = None
area_model = None
seed_model = None
selected_area_label = None
progress_bar = None
status_label = None

selected_area_prim = None
generated_points = []
is_processing = False

pcg_types = {
    "Trees":  {"emoji": "🌲", "sources": [], "preview_scale": (0.45, 0.45, 3.0), "color": (0.1, 0.8, 0.2)},
    "Stones": {"emoji": "🪨", "sources": [], "preview_scale": (1.2, 1.2, 0.45), "color": (0.45, 0.45, 0.45)},
    "Bushes": {"emoji": "🌿", "sources": [], "preview_scale": (0.9, 0.9, 0.8), "color": (0.05, 0.6, 0.15)},
    "Grass":  {"emoji": "🍃", "sources": [], "preview_scale": (0.25, 0.25, 0.7), "color": (0.25, 0.9, 0.25)},
    "Custom": {"emoji": "⭐", "sources": [], "preview_scale": (0.8, 0.8, 0.8), "color": (1.0, 0.8, 0.1)},
}


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def refresh_stage():
    global stage
    stage = omni.usd.get_context().get_stage()


def set_status(text, progress=None):
    if status_label:
        status_label.text = text
    if progress_bar and progress is not None:
        progress_bar.model.set_value(float(progress))
    print(text)


def clear_path(path):
    refresh_stage()
    prim = stage.GetPrimAtPath(path)
    if prim and prim.IsValid():
        stage.RemovePrim(path)


def ensure_xform(path):
    refresh_stage()
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        UsdGeom.Xform.Define(stage, path)


def get_selected_paths():
    return list(omni.usd.get_context().get_selection().get_selected_prim_paths())


def short_name(path):
    return str(path).split("/")[-1]


def make_unique_path(base_path):
    path = base_path
    index = 1
    while stage.GetPrimAtPath(path):
        path = f"{base_path}_{index}"
        index += 1
    return path


def fallback_height(x, y):
    return math.sin(x * 0.08) * 1.5 + math.cos(y * 0.05) * 1.2


# --------------------------------------------------
# Selected Area Bounds
# --------------------------------------------------

def get_world_bounds(prim_path):
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return None

    cache = UsdGeom.BBoxCache(
        0,
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        useExtentsHint=True
    )

    bbox = cache.ComputeWorldBound(prim)
    box = bbox.ComputeAlignedBox()

    min_pt = box.GetMin()
    max_pt = box.GetMax()

    return min_pt, max_pt


def set_selected_area():
    global selected_area_prim

    paths = get_selected_paths()
    if not paths:
        selected_area_prim = None
        if selected_area_label:
            selected_area_label.text = "Selected Area: None"
        print("⚠️ Select a plane/landscape first.")
        return

    selected_area_prim = paths[0]

    if selected_area_label:
        selected_area_label.text = f"Selected Area: {short_name(selected_area_prim)}"

    print("✅ Selected spawn area:", selected_area_prim)


def clear_selected_area():
    global selected_area_prim
    selected_area_prim = None

    if selected_area_label:
        selected_area_label.text = "Selected Area: None"

    print("🧹 Cleared selected spawn area.")


def get_random_xy(area_size):
    if selected_area_prim:
        bounds = get_world_bounds(selected_area_prim)
        if bounds:
            min_pt, max_pt = bounds
            x = random.uniform(min_pt[0], max_pt[0])
            y = random.uniform(min_pt[1], max_pt[1])
            return x, y

    return (
        random.uniform(-area_size / 2, area_size / 2),
        random.uniform(-area_size / 2, area_size / 2)
    )


# --------------------------------------------------
# Materials / Preview Colors
# --------------------------------------------------

def create_preview_material(name, color):
    ensure_xform(MATERIAL_ROOT)

    mat_path = f"{MATERIAL_ROOT}/{name}_Preview_Mat"

    if stage.GetPrimAtPath(mat_path):
        return mat_path

    material = UsdShade.Material.Define(stage, mat_path)
    shader = UsdShade.Shader.Define(stage, mat_path + "/Shader")

    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.45)

    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")

    return mat_path


def bind_material(prim, material_path):
    material = UsdShade.Material.Get(stage, material_path)
    UsdShade.MaterialBindingAPI(prim).Bind(material)


def create_preview_cube(path, pos, scale, color, type_name):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)

    xf = UsdGeom.Xformable(cube.GetPrim())
    xf.ClearXformOpOrder()
    xf.AddTranslateOp().Set(Gf.Vec3d(*pos))
    xf.AddScaleOp().Set(Gf.Vec3f(*scale))

    mat_path = create_preview_material(type_name, color)
    bind_material(cube.GetPrim(), mat_path)


# --------------------------------------------------
# Ground Raycast
# --------------------------------------------------

def raycast_ground(x, y, start_z=500.0, max_distance=1500.0):
    try:
        import omni.physx
        scene_query = omni.physx.get_physx_scene_query_interface()

        origin = Gf.Vec3f(x, y, start_z)
        direction = Gf.Vec3f(0, 0, -1)

        hit = scene_query.raycast_closest(origin, direction, max_distance)

        if hit and hit.get("hit", False):
            pos = hit["position"]
            normal = hit.get("normal", Gf.Vec3f(0, 0, 1))
            return (pos[0], pos[1], pos[2]), normal, True

    except Exception:
        pass

    z = fallback_height(x, y)
    return (x, y, z), Gf.Vec3f(0, 0, 1), False


# --------------------------------------------------
# Placement Rules
# --------------------------------------------------

def is_far_enough(pos, existing_points, min_distance):
    if min_distance <= 0:
        return True

    for p in existing_points:
        dx = pos[0] - p[0]
        dy = pos[1] - p[1]
        if math.sqrt(dx * dx + dy * dy) < min_distance:
            return False

    return True


# --------------------------------------------------
# Add / Clear Items
# --------------------------------------------------

def add_selected_to_type(type_name):
    paths = get_selected_paths()

    if not paths:
        print(f"⚠️ Select object(s) first for {type_name}.")
        return

    for p in paths:
        if p not in pcg_types[type_name]["sources"]:
            pcg_types[type_name]["sources"].append(p)

    update_labels()
    print(f"✅ Added to {type_name}: {paths}")


def clear_group_items(type_name):
    pcg_types[type_name]["sources"].clear()
    update_labels()
    print(f"🧹 Cleared {type_name} items.")


def clear_all_added_items():
    for data in pcg_types.values():
        data["sources"].clear()

    update_labels()
    print("🧹 Cleared all added item lists.")


def update_labels():
    for type_name, data in pcg_types.items():
        label = data.get("label")
        if not label:
            continue

        sources = data["sources"]

        if not sources:
            label.text = f"{data['emoji']} {type_name} Added: 0"
        else:
            names = "\n".join([f"{data['emoji']} {short_name(p)}" for p in sources])
            label.text = f"{data['emoji']} {type_name} Added: {len(sources)}\n{names}"


# --------------------------------------------------
# Async Preview
# --------------------------------------------------

async def generate_preview_async():
    global generated_points, is_processing

    if is_processing:
        print("⚠️ Already processing.")
        return

    is_processing = True
    generated_points = []

    refresh_stage()
    clear_path(PREVIEW_ROOT)
    ensure_xform(PREVIEW_ROOT)

    area_size = area_model.model.get_value_as_float()
    seed = seed_model.model.get_value_as_int()
    random.seed(seed)

    total_target = sum(data["count_model"].model.get_value_as_int() for data in pcg_types.values())

    if total_target <= 0:
        set_status("⚠️ No items to preview.", 0)
        is_processing = False
        return

    completed = 0
    ray_hits = 0
    fallbacks = 0

    for type_name, data in pcg_types.items():
        count = data["count_model"].model.get_value_as_int()

        if count <= 0:
            continue

        sources = data["sources"]
        min_distance = data["min_distance_model"].model.get_value_as_float()
        height_offset = data["height_offset_model"].model.get_value_as_float()
        scale_min = data["scale_min_model"].model.get_value_as_float()
        scale_max = data["scale_max_model"].model.get_value_as_float()
        random_rotation = data["rotation_model"].model.get_value_as_bool()

        if scale_max < scale_min:
            scale_max = scale_min

        type_points = []
        type_root = f"{PREVIEW_ROOT}/{type_name}"
        ensure_xform(type_root)

        attempts = 0
        max_attempts = max(count * 60, 100)

        while len(type_points) < count and attempts < max_attempts:
            attempts += 1

            x, y = get_random_xy(area_size)
            ground_pos, ground_normal, ray_hit = raycast_ground(x, y)

            if ray_hit:
                ray_hits += 1
            else:
                fallbacks += 1

            pos = (ground_pos[0], ground_pos[1], ground_pos[2] + height_offset)

            if not is_far_enough(pos, type_points, min_distance):
                continue

            rot_z = random.uniform(0, 360) if random_rotation else 0
            scale_multiplier = random.uniform(scale_min, scale_max)
            source = random.choice(sources) if sources else None

            item = {
                "type": type_name,
                "source": source,
                "pos": pos,
                "rot": (0, 0, rot_z),
                "scale_multiplier": scale_multiplier,
                "normal": ground_normal,
            }

            generated_points.append(item)
            type_points.append(pos)

            create_preview_cube(
                f"{type_root}/{type_name}_Preview_{len(type_points)}",
                pos,
                data["preview_scale"],
                data["color"],
                type_name
            )

            completed += 1
            set_status(f"Generating preview... {completed}/{total_target}", completed / total_target)

            if completed % 20 == 0:
                await omni.kit.app.get_app().next_update_async()

        if len(type_points) < count:
            print(f"⚠️ Only placed {len(type_points)}/{count} for {type_name}. Try lower Min Distance.")

    set_status(f"✅ Preview done. Ray hits: {ray_hits}, fallback: {fallbacks}", 1.0)
    is_processing = False


def generate_preview():
    asyncio.ensure_future(generate_preview_async())


# --------------------------------------------------
# Final Spawn
# --------------------------------------------------

def get_existing_scale_from_copied_prim(path):
    prim = stage.GetPrimAtPath(path)
    xf = UsdGeom.Xformable(prim)

    final_scale = Gf.Vec3f(1, 1, 1)

    for op in xf.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeScale:
            value = op.Get()
            if value:
                final_scale[0] *= value[0]
                final_scale[1] *= value[1]
                final_scale[2] *= value[2]

    return final_scale


def apply_transform_preserve_scale(path, pos, rot, scale_multiplier):
    prim = stage.GetPrimAtPath(path)
    xf = UsdGeom.Xformable(prim)

    original_scale = get_existing_scale_from_copied_prim(path)

    final_scale = (
        original_scale[0] * scale_multiplier,
        original_scale[1] * scale_multiplier,
        original_scale[2] * scale_multiplier,
    )

    xf.ClearXformOpOrder()
    xf.SetResetXformStack(True)

    xf.AddTranslateOp().Set(Gf.Vec3d(*pos))
    xf.AddRotateXYZOp().Set(Gf.Vec3f(*rot))
    xf.AddScaleOp().Set(Gf.Vec3f(*final_scale))


def duplicate_scene_prim(source_path, target_path, pos, rot, scale_multiplier):
    refresh_stage()

    source_prim = stage.GetPrimAtPath(source_path)

    if not source_prim or not source_prim.IsValid():
        print("❌ Invalid source:", source_path)
        return False

    root_layer = stage.GetRootLayer()
    target_path = make_unique_path(target_path)

    try:
        Sdf.CopySpec(
            root_layer,
            Sdf.Path(source_path),
            root_layer,
            Sdf.Path(target_path)
        )

        apply_transform_preserve_scale(target_path, pos, rot, scale_multiplier)
        return True

    except Exception as e:
        print("❌ Copy failed:", source_path)
        print(e)
        return False


async def final_spawn_async():
    global is_processing

    if is_processing:
        print("⚠️ Already processing.")
        return

    if not generated_points:
        set_status("⚠️ Generate preview first.", 0)
        return

    is_processing = True
    refresh_stage()

    clear_path(FINAL_ROOT)
    ensure_xform(FINAL_ROOT)

    for type_name in pcg_types.keys():
        ensure_xform(f"{FINAL_ROOT}/{type_name}")

    total = len(generated_points)
    spawned = 0
    skipped = 0

    for i, item in enumerate(generated_points):
        if not item["source"]:
            skipped += 1
        else:
            target_path = f"{FINAL_ROOT}/{item['type']}/{item['type']}_{i}"

            ok = duplicate_scene_prim(
                item["source"],
                target_path,
                item["pos"],
                item["rot"],
                item["scale_multiplier"]
            )

            if ok:
                spawned += 1
            else:
                skipped += 1

        set_status(f"Final spawning... {i + 1}/{total}", (i + 1) / total)

        if i % 10 == 0:
            await omni.kit.app.get_app().next_update_async()

    clear_path(PREVIEW_ROOT)

    set_status(f"✅ Final spawn done. Spawned: {spawned}, skipped: {skipped}", 1.0)
    is_processing = False


def final_spawn():
    asyncio.ensure_future(final_spawn_async())


# --------------------------------------------------
# Clear Generated
# --------------------------------------------------

def clear_preview():
    clear_path(PREVIEW_ROOT)
    generated_points.clear()
    set_status("🧹 Preview cleared.", 0)


def clear_final():
    clear_path(FINAL_ROOT)
    set_status("🧹 Final spawn cleared.", 0)


def clear_all_generated():
    clear_preview()
    clear_final()


# --------------------------------------------------
# GUI
# --------------------------------------------------

def build_gui():
    global pcg_window, area_model, seed_model
    global selected_area_label, progress_bar, status_label

    pcg_window = ui.Window(
        "Generic Isaac Sim PCG Scatter Tool",
        width=560,
        height=880
    )

    with pcg_window.frame:
        with ui.ScrollingFrame(
            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON
        ):
            with ui.VStack(spacing=10):

                ui.Label("🌍 Generic Isaac Sim PCG Scatter Tool", height=32)
                ui.Label("Grouped PCG scatter with async progress and selected-area spawning.")

                ui.Separator()

                for type_name, data in pcg_types.items():
                    with ui.CollapsableFrame(title=f"{data['emoji']} {type_name} Group", collapsed=False):
                        with ui.VStack(spacing=6):

                            ui.Button(
                                f"Add Selected Scene Objects as {type_name}",
                                height=30,
                                clicked_fn=lambda t=type_name: add_selected_to_type(t)
                            )

                            ui.Button(
                                f"Clear {type_name} Items",
                                height=28,
                                clicked_fn=lambda t=type_name: clear_group_items(t)
                            )

                            data["label"] = ui.Label(
                                f"{data['emoji']} {type_name} Added: 0",
                                height=60
                            )

                            with ui.HStack(height=28):
                                ui.Label("Count", width=170)
                                data["count_model"] = ui.IntDrag(min=0, max=10000)
                                data["count_model"].model.set_value(0)

                            with ui.HStack(height=28):
                                ui.Label("Min Distance", width=170)
                                data["min_distance_model"] = ui.FloatDrag(min=0.0, max=100.0)
                                data["min_distance_model"].model.set_value(2.0)

                            with ui.HStack(height=28):
                                ui.Label("Height Offset", width=170)
                                data["height_offset_model"] = ui.FloatDrag(min=-10.0, max=10.0)
                                data["height_offset_model"].model.set_value(0.0)

                            with ui.HStack(height=28):
                                ui.Label("Scale Min", width=170)
                                data["scale_min_model"] = ui.FloatDrag(min=0.01, max=10.0)
                                data["scale_min_model"].model.set_value(1.0)

                            with ui.HStack(height=28):
                                ui.Label("Scale Max", width=170)
                                data["scale_max_model"] = ui.FloatDrag(min=0.01, max=10.0)
                                data["scale_max_model"].model.set_value(1.0)

                            with ui.HStack(height=28):
                                ui.Label("Random Rotation", width=170)
                                data["rotation_model"] = ui.CheckBox()
                                data["rotation_model"].model.set_value(True)

                pcg_types["Trees"]["count_model"].model.set_value(100)
                pcg_types["Stones"]["count_model"].model.set_value(60)

                ui.Separator()

                with ui.CollapsableFrame(title="🟩 Spawn Selected Area Only", collapsed=False):
                    with ui.VStack(spacing=6):

                        ui.Label("Select a plane/landscape in the scene, then click Set Spawn Area.")
                        selected_area_label = ui.Label("Selected Area: None", height=26)

                        ui.Button("Set Selected Object as Spawn Area", height=32, clicked_fn=set_selected_area)
                        ui.Button("Clear Selected Spawn Area", height=30, clicked_fn=clear_selected_area)

                        ui.Label("If no area is selected, it uses Area Size around world origin.")

                with ui.CollapsableFrame(title="⚙️ Global Scatter Settings", collapsed=False):
                    with ui.VStack(spacing=8):

                        with ui.HStack(height=30):
                            ui.Label("Area Size", width=170)
                            area_model = ui.FloatDrag(min=10, max=5000)
                            area_model.model.set_value(100)

                        with ui.HStack(height=30):
                            ui.Label("Random Seed", width=170)
                            seed_model = ui.IntDrag(min=1, max=999999)
                            seed_model.model.set_value(10)

                with ui.CollapsableFrame(title="🚀 Actions + Progress", collapsed=False):
                    with ui.VStack(spacing=8):

                        status_label = ui.Label("Idle", height=26)

                        progress_bar = ui.ProgressBar(height=24)
                        progress_bar.model.set_value(0.0)

                        ui.Button("Generate Colored Ground Preview", height=40, clicked_fn=generate_preview)
                        ui.Button("Final Spawn Scene Items", height=40, clicked_fn=final_spawn)

                        ui.Separator()

                        ui.Button("Clear Preview Only", height=32, clicked_fn=clear_preview)
                        ui.Button("Clear Final Spawn Only", height=32, clicked_fn=clear_final)
                        ui.Button("Clear Preview + Final Spawn", height=32, clicked_fn=clear_all_generated)
                        ui.Button("Clear All Added Item Lists", height=32, clicked_fn=clear_all_added_items)

                with ui.CollapsableFrame(title="📘 Usage Guide", collapsed=True):
                    with ui.VStack(spacing=4):
                        ui.Label("1. Add trees/stones/bushes/grass/custom objects to scene.")
                        ui.Label("2. Select object(s), then add them to the matching PCG group.")
                        ui.Label("3. Optional: select landscape/plane and set it as spawn area.")
                        ui.Label("4. Set counts and per-group settings.")
                        ui.Label("5. Generate preview, then final spawn.")
                        ui.Label("Note: ground object needs collision for accurate raycast placement.")


build_gui()
update_labels()
set_status("✅ PCG tool loaded.", 0)

print("✅ Full upgraded PCG tool loaded.")