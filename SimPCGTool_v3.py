import omni.usd
import omni.ui as ui
import omni.kit.app
from pxr import Usd, UsdGeom, Gf, Sdf, UsdShade
import random, math, asyncio

stage = omni.usd.get_context().get_stage()

PREVIEW_ROOT = "/World/PCG_Preview"
FINAL_ROOT = "/World/PCG_Final"
MATERIAL_ROOT = "/World/PCG_Materials"

pcg_window = None
area_model = None
seed_model = None
selected_area_label = None
obstacle_label = None
progress_bar = None
status_label = None

selected_area_prim = None
obstacle_prims = []
generated_points = []
is_processing = False
preview_serial = 0

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
        progress_bar.model.set_value(float(max(0.0, min(1.0, progress))))
    print(text)


async def yield_ui():
    await omni.kit.app.get_app().next_update_async()


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


def safe_name(name):
    return str(name).replace(" ", "_").replace("-", "_")


def make_unique_path(base_path):
    path = base_path
    index = 1
    while stage.GetPrimAtPath(path):
        path = f"{base_path}_{index}"
        index += 1
    return path


def get_world_pos(prim):
    xf = UsdGeom.Xformable(prim)
    pos = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
    return Gf.Vec3d(pos[0], pos[1], pos[2])


def get_world_bounds(prim_path):
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return None

    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        useExtentsHint=True
    )

    box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
    return box.GetMin(), box.GetMax()


def get_asset_radius_and_bottom_offset(source_path):
    if not source_path:
        return 1.0, 0.0

    bounds = get_world_bounds(source_path)
    prim = stage.GetPrimAtPath(source_path)

    if not bounds or not prim or not prim.IsValid():
        return 1.0, 0.0

    mn, mx = bounds
    pivot = get_world_pos(prim)

    width = max(abs(mx[0] - mn[0]), abs(mx[1] - mn[1]))
    radius = max(0.5, width * 0.5)

    bottom_offset = mn[2] - pivot[2]
    return radius, bottom_offset


# --------------------------------------------------
# Area / Obstacle Selection
# --------------------------------------------------

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


def add_selected_obstacles():
    paths = get_selected_paths()

    for p in paths:
        if p not in obstacle_prims:
            obstacle_prims.append(p)

    update_obstacle_label()
    print("✅ Added obstacles:", paths)


def clear_obstacles():
    obstacle_prims.clear()
    update_obstacle_label()
    print("🧹 Cleared obstacle list.")


def update_obstacle_label():
    if not obstacle_label:
        return

    if not obstacle_prims:
        obstacle_label.text = "Obstacles Added: 0"
    else:
        names = "\n".join(["🚧 " + short_name(p) for p in obstacle_prims])
        obstacle_label.text = f"Obstacles Added: {len(obstacle_prims)}\n{names}"


def get_spawn_bounds(area_size):
    if selected_area_prim:
        bounds = get_world_bounds(selected_area_prim)
        if bounds:
            return bounds

    half = area_size * 0.5
    return Gf.Vec3d(-half, -half, 0), Gf.Vec3d(half, half, 0)


# --------------------------------------------------
# Materials / Preview
# --------------------------------------------------

def create_preview_material(name, color):
    ensure_xform(MATERIAL_ROOT)

    mat_path = f"{MATERIAL_ROOT}/{safe_name(name)}_Preview_Mat"

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


def create_preview_marker(path, pos, scale, color, type_name):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)

    xf = UsdGeom.Xformable(cube.GetPrim())
    xf.ClearXformOpOrder()
    xf.AddTranslateOp().Set(Gf.Vec3d(*pos))
    xf.AddScaleOp().Set(Gf.Vec3f(*scale))

    mat_path = create_preview_material(type_name, color)
    bind_material(cube.GetPrim(), mat_path)


def get_preview_marker_world_pos(path):
    prim = stage.GetPrimAtPath(path)

    if not prim or not prim.IsValid():
        return None

    xf = UsdGeom.Xformable(prim)
    world = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    pos = world.ExtractTranslation()

    return (pos[0], pos[1], pos[2])


def sync_generated_points_from_preview():
    refresh_stage()

    updated = 0
    missing = 0

    for item in generated_points:
        preview_path = item.get("preview_path")

        if not preview_path:
            continue

        pos = get_preview_marker_world_pos(preview_path)

        if pos is None:
            missing += 1
            continue

        item["pos"] = pos
        updated += 1

    print(f"✅ Synced preview marker edits: {updated} updated, {missing} missing.")
    return updated


# --------------------------------------------------
# Ground Raycast / Stick to Ground
# --------------------------------------------------

def fallback_height(x, y):
    if selected_area_prim:
        bounds = get_world_bounds(selected_area_prim)
        if bounds:
            mn, mx = bounds
            return mx[2]
    return 0.0


def raycast_ground(x, y, start_z=1000.0, max_distance=3000.0):
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
# Obstacle Avoidance / Spacing
# --------------------------------------------------

def point_inside_obstacle(pos, radius, obstacle_padding):
    x, y, _ = pos

    for obstacle_path in obstacle_prims:
        bounds = get_world_bounds(obstacle_path)
        if not bounds:
            continue

        mn, mx = bounds

        if (
            x >= mn[0] - radius - obstacle_padding and
            x <= mx[0] + radius + obstacle_padding and
            y >= mn[1] - radius - obstacle_padding and
            y <= mx[1] + radius + obstacle_padding
        ):
            return True

    return False


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
# Clustered Placement
# --------------------------------------------------

def create_cluster_centers(bounds, cluster_count):
    mn, mx = bounds
    centers = []

    for _ in range(max(1, cluster_count)):
        x = random.uniform(mn[0], mx[0])
        y = random.uniform(mn[1], mx[1])
        centers.append((x, y))

    return centers


def sample_clustered_xy(bounds, centers, cluster_strength, cluster_radius):
    mn, mx = bounds

    if centers and random.random() < cluster_strength:
        cx, cy = random.choice(centers)
        x = random.gauss(cx, cluster_radius)
        y = random.gauss(cy, cluster_radius)
    else:
        x = random.uniform(mn[0], mx[0])
        y = random.uniform(mn[1], mx[1])

    x = max(mn[0], min(mx[0], x))
    y = max(mn[1], min(mx[1], y))

    return x, y


# --------------------------------------------------
# Add / Clear PCG Items
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
# Preview Generation
# --------------------------------------------------

async def generate_preview_async(replace_existing=True):
    global generated_points, is_processing, preview_serial

    if is_processing:
        print("⚠️ Already processing.")
        return

    is_processing = True

    refresh_stage()

    if replace_existing:
        generated_points = []
        clear_path(PREVIEW_ROOT)

    ensure_xform(PREVIEW_ROOT)

    area_size = area_model.model.get_value_as_float()
    seed = seed_model.model.get_value_as_int()
    random.seed(seed + preview_serial)

    bounds = get_spawn_bounds(area_size)

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
        obstacle_padding = data["obstacle_padding_model"].model.get_value_as_float()

        scale_min = data["scale_min_model"].model.get_value_as_float()
        scale_max = data["scale_max_model"].model.get_value_as_float()
        random_rotation = data["rotation_model"].model.get_value_as_bool()

        cluster_count = data["cluster_count_model"].model.get_value_as_int()
        cluster_strength = data["cluster_strength_model"].model.get_value_as_float()
        cluster_radius = data["cluster_radius_model"].model.get_value_as_float()

        if scale_max < scale_min:
            scale_max = scale_min

        type_root = f"{PREVIEW_ROOT}/{safe_name(type_name)}"
        ensure_xform(type_root)

        centers = create_cluster_centers(bounds, cluster_count)
        type_points = []

        if not replace_existing:
            for item in generated_points:
                if item.get("type") == type_name:
                    type_points.append(item["pos"])

        attempts = 0
        max_attempts = max(count * 100, 200)

        while len(type_points) < count + (0 if replace_existing else len([p for p in generated_points if p.get("type") == type_name])) and attempts < max_attempts:
            attempts += 1

            x, y = sample_clustered_xy(bounds, centers, cluster_strength, cluster_radius)
            ground_pos, ground_normal, ray_hit = raycast_ground(x, y)

            if ray_hit:
                ray_hits += 1
            else:
                fallbacks += 1

            source = random.choice(sources) if sources else None
            asset_radius, bottom_offset = get_asset_radius_and_bottom_offset(source) if source else (1.0, 0.0)

            pivot_z = ground_pos[2] - bottom_offset + height_offset
            pos = (ground_pos[0], ground_pos[1], pivot_z)

            if point_inside_obstacle(pos, asset_radius, obstacle_padding):
                continue

            if not is_far_enough(pos, type_points, min_distance + asset_radius):
                continue

            rot_z = random.uniform(0, 360) if random_rotation else 0
            scale_multiplier = random.uniform(scale_min, scale_max)

            preview_serial += 1
            preview_path = f"{type_root}/{safe_name(type_name)}_Preview_{preview_serial}"

            item = {
                "type": type_name,
                "source": source,
                "pos": pos,
                "rot": (0, 0, rot_z),
                "scale_multiplier": scale_multiplier,
                "normal": ground_normal,
                "preview_path": preview_path,
            }

            generated_points.append(item)
            type_points.append(pos)

            create_preview_marker(
                preview_path,
                pos,
                data["preview_scale"],
                data["color"],
                type_name
            )

            completed += 1
            set_status(f"Generating grouped preview... {completed}/{total_target}", completed / total_target)

            if completed % 15 == 0:
                await yield_ui()

        if completed < total_target:
            pass

    set_status(f"✅ Preview done. Move preview markers if needed, then Final Spawn. Ray hits: {ray_hits}, fallback: {fallbacks}", 1.0)
    is_processing = False


def generate_replace_preview():
    asyncio.ensure_future(generate_preview_async(replace_existing=True))


def generate_add_preview():
    asyncio.ensure_future(generate_preview_async(replace_existing=False))


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


async def final_spawn_async(replace_existing=False):
    global is_processing

    if is_processing:
        print("⚠️ Already processing.")
        return

    if not generated_points:
        set_status("⚠️ Generate preview first.", 0)
        return

    is_processing = True
    refresh_stage()

    sync_generated_points_from_preview()

    if replace_existing:
        clear_path(FINAL_ROOT)

    ensure_xform(FINAL_ROOT)

    for type_name in pcg_types.keys():
        ensure_xform(f"{FINAL_ROOT}/{safe_name(type_name)}")

    total = len(generated_points)
    spawned = 0
    skipped = 0

    for i, item in enumerate(generated_points):
        if not item["source"]:
            skipped += 1
        else:
            type_name = safe_name(item["type"])
            target_path = f"{FINAL_ROOT}/{type_name}/{type_name}_{i}"

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

        set_status(f"Final spawning from edited preview... {i + 1}/{total}", (i + 1) / total)

        if i % 10 == 0:
            await yield_ui()

    set_status(f"✅ Final spawn done. Spawned: {spawned}, skipped: {skipped}", 1.0)
    is_processing = False


def final_spawn_append():
    asyncio.ensure_future(final_spawn_async(replace_existing=False))


def final_spawn_replace():
    asyncio.ensure_future(final_spawn_async(replace_existing=True))


# --------------------------------------------------
# Clear
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
    global selected_area_label, obstacle_label, progress_bar, status_label

    pcg_window = ui.Window("Exciting Isaac Sim PCG Scatter Tool", width=620, height=920)

    with pcg_window.frame:
        with ui.ScrollingFrame(
            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON
        ):
            with ui.VStack(spacing=10):

                ui.Label("🌍 Exciting Isaac Sim PCG Scatter Tool", height=32)
                ui.Label("Clustered groups, obstacle avoidance, editable preview, append/replace final spawn.")

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

                            data["label"] = ui.Label(f"{data['emoji']} {type_name} Added: 0", height=60)

                            with ui.HStack(height=28):
                                ui.Label("Count", width=200)
                                data["count_model"] = ui.IntDrag(min=0, max=10000)
                                data["count_model"].model.set_value(0)

                            with ui.HStack(height=28):
                                ui.Label("Min Distance", width=200)
                                data["min_distance_model"] = ui.FloatDrag(min=0.0, max=100.0)
                                data["min_distance_model"].model.set_value(2.0)

                            with ui.HStack(height=28):
                                ui.Label("Obstacle Padding", width=200)
                                data["obstacle_padding_model"] = ui.FloatDrag(min=0.0, max=100.0)
                                data["obstacle_padding_model"].model.set_value(1.0)

                            with ui.HStack(height=28):
                                ui.Label("Height Offset", width=200)
                                data["height_offset_model"] = ui.FloatDrag(min=-20.0, max=20.0)
                                data["height_offset_model"].model.set_value(0.0)

                            with ui.HStack(height=28):
                                ui.Label("Scale Min", width=200)
                                data["scale_min_model"] = ui.FloatDrag(min=0.01, max=10.0)
                                data["scale_min_model"].model.set_value(1.0)

                            with ui.HStack(height=28):
                                ui.Label("Scale Max", width=200)
                                data["scale_max_model"] = ui.FloatDrag(min=0.01, max=10.0)
                                data["scale_max_model"].model.set_value(1.0)

                            with ui.HStack(height=28):
                                ui.Label("Cluster Count", width=200)
                                data["cluster_count_model"] = ui.IntDrag(min=1, max=100)
                                data["cluster_count_model"].model.set_value(4)

                            with ui.HStack(height=28):
                                ui.Label("Cluster Strength", width=200)
                                data["cluster_strength_model"] = ui.FloatDrag(min=0.0, max=1.0)
                                data["cluster_strength_model"].model.set_value(0.85)

                            with ui.HStack(height=28):
                                ui.Label("Cluster Radius", width=200)
                                data["cluster_radius_model"] = ui.FloatDrag(min=0.1, max=200.0)
                                data["cluster_radius_model"].model.set_value(8.0)

                            with ui.HStack(height=28):
                                ui.Label("Random Rotation", width=200)
                                data["rotation_model"] = ui.CheckBox()
                                data["rotation_model"].model.set_value(True)

                pcg_types["Trees"]["count_model"].model.set_value(80)
                pcg_types["Stones"]["count_model"].model.set_value(40)
                pcg_types["Bushes"]["count_model"].model.set_value(30)

                ui.Separator()

                with ui.CollapsableFrame(title="🟩 Spawn Area", collapsed=False):
                    with ui.VStack(spacing=6):
                        selected_area_label = ui.Label("Selected Area: None", height=26)
                        ui.Button("Set Selected Object as Spawn Area", height=32, clicked_fn=set_selected_area)
                        ui.Button("Clear Selected Spawn Area", height=30, clicked_fn=clear_selected_area)

                with ui.CollapsableFrame(title="🚧 Obstacles To Avoid", collapsed=False):
                    with ui.VStack(spacing=6):
                        ui.Label("Select objects to avoid, then add them here.")
                        ui.Button("Add Selected Objects as Obstacles", height=32, clicked_fn=add_selected_obstacles)
                        ui.Button("Clear Obstacle List", height=30, clicked_fn=clear_obstacles)
                        obstacle_label = ui.Label("Obstacles Added: 0", height=100)

                with ui.CollapsableFrame(title="⚙️ Global Scatter Settings", collapsed=False):
                    with ui.VStack(spacing=8):
                        with ui.HStack(height=30):
                            ui.Label("Area Size Fallback", width=200)
                            area_model = ui.FloatDrag(min=10, max=5000)
                            area_model.model.set_value(100)

                        with ui.HStack(height=30):
                            ui.Label("Random Seed", width=200)
                            seed_model = ui.IntDrag(min=1, max=999999)
                            seed_model.model.set_value(10)

                with ui.CollapsableFrame(title="🚀 Actions + Progress", collapsed=False):
                    with ui.VStack(spacing=8):
                        status_label = ui.Label("Idle", height=26)
                        progress_bar = ui.ProgressBar(height=24)
                        progress_bar.model.set_value(0.0)

                        ui.Button("Replace Preview With New Grouped Layout", height=40, clicked_fn=generate_replace_preview)
                        ui.Button("Add More Preview Markers", height=36, clicked_fn=generate_add_preview)

                        ui.Separator()

                        ui.Button("Final Spawn APPEND From Edited Preview", height=40, clicked_fn=final_spawn_append)
                        ui.Button("Final Spawn REPLACE From Edited Preview", height=40, clicked_fn=final_spawn_replace)

                        ui.Separator()

                        ui.Button("Clear Preview Only", height=32, clicked_fn=clear_preview)
                        ui.Button("Clear Final Spawn Only", height=32, clicked_fn=clear_final)
                        ui.Button("Clear Preview + Final Spawn", height=32, clicked_fn=clear_all_generated)
                        ui.Button("Clear All Added Item Lists", height=32, clicked_fn=clear_all_added_items)

                with ui.CollapsableFrame(title="📘 Usage Guide", collapsed=True):
                    with ui.VStack(spacing=4):
                        ui.Label("1. Set Spawn Area.")
                        ui.Label("2. Add Trees/Stones/Bushes/etc.")
                        ui.Label("3. Add obstacle objects if needed.")
                        ui.Label("4. Generate preview.")
                        ui.Label("5. Move preview markers manually if needed.")
                        ui.Label("6. Final Spawn uses edited preview marker positions.")
                        ui.Label("Append = keeps previous final items.")
                        ui.Label("Replace = clears previous final items first.")


build_gui()
update_labels()
update_obstacle_label()
set_status("✅ Exciting PCG tool loaded.", 0)

print("✅ PCG loaded: editable preview, append/replace final spawn.")