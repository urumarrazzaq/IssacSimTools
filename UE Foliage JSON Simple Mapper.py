import json, os, asyncio, re
import omni.usd, omni.ui as ui, omni.kit.app, omni.kit.commands
from pxr import UsdGeom, Gf

WINDOW_TITLE = "UE Foliage JSON Mapper + Ground Snap"
window_ref = None
MESH_NAME_TO_ISAAC_PATH = {}

def get_stage():
    return omni.usd.get_context().get_stage()

async def update_ui():
    await omni.kit.app.get_app().next_update_async()

def get_selected_prim_path():
    sel = omni.usd.get_context().get_selection().get_selected_prim_paths()
    return sel[0] if sel else ""

def ensure_xform(stage, path):
    if not stage.GetPrimAtPath(path).IsValid():
        UsdGeom.Xform.Define(stage, path)

def clear_path(stage, path):
    prim = stage.GetPrimAtPath(path)
    if prim and prim.IsValid():
        stage.RemovePrim(path)

def duplicate_prim(source_path, target_path):
    omni.kit.commands.execute("CopyPrim", path_from=source_path, path_to=target_path)

def extract_mesh_name(ue_mesh_path):
    last = ue_mesh_path.split("/")[-1]
    return last.split(".")[-1] if "." in last else last

def sanitize_name(name):
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)

def natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]

def base_group_name(name):
    return re.sub(r"_[0-9]+$", "", name)

def raycast_ground_z(x, y, z, start_height, end_depth):
    try:
        import omni.physx
        query = omni.physx.get_physx_scene_query_interface()
        origin = Gf.Vec3f(x, y, z + start_height)
        direction = Gf.Vec3f(0, 0, -1)
        hit = query.raycast_closest(origin, direction, start_height + end_depth)

        if hit and hit.get("hit", False):
            pos = hit.get("position")
            return float(pos[2])
    except Exception as e:
        print("Ground snap failed. Enable collision on ground mesh:", e)

    return None

def apply_transform(target_path, item, unit_scale, asset_scale, flip_y, yaw_offset,
                    snap_ground, ray_start_height, ray_end_depth, ground_offset):

    stage = get_stage()
    prim = stage.GetPrimAtPath(target_path)
    if not prim or not prim.IsValid():
        return False

    loc = item["location"]
    rot = item["rotation"]
    scl = item["scale"]

    x = loc["x"] * unit_scale
    y = loc["y"] * unit_scale
    z = loc["z"] * unit_scale

    pitch = rot["pitch"]
    yaw = rot["yaw"] + yaw_offset
    roll = rot["roll"]

    if flip_y:
        y *= -1
        yaw *= -1
        roll *= -1

    if snap_ground:
        ground_z = raycast_ground_z(x, y, z, ray_start_height, ray_end_depth)
        if ground_z is not None:
            z = ground_z + ground_offset

    sx = scl["x"] * asset_scale
    sy = scl["y"] * asset_scale
    sz = scl["z"] * asset_scale

    xf = UsdGeom.Xformable(prim)
    xf.ClearXformOpOrder()
    xf.SetResetXformStack(True)
    xf.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
    xf.AddRotateXYZOp().Set(Gf.Vec3f(roll, pitch, yaw))
    xf.AddScaleOp().Set(Gf.Vec3f(sx, sy, sz))

    return True

class FoliageWindow:
    def __init__(self):
        self.window = ui.Window(WINDOW_TITLE, width=820, height=780)

        self.json_path_model = ui.SimpleStringModel(r"C:/Temp/foliage_export.json")
        self.output_root_model = ui.SimpleStringModel("/World/UE_Foliage_Meshes")

        self.unit_scale_model = ui.SimpleFloatModel(0.01)
        self.asset_scale_model = ui.SimpleFloatModel(0.01)

        self.flip_y_model = ui.SimpleBoolModel(False)
        self.yaw_offset_model = ui.SimpleFloatModel(0.0)

        self.snap_ground_model = ui.SimpleBoolModel(True)
        self.ray_start_height_model = ui.SimpleFloatModel(200.0)
        self.ray_end_depth_model = ui.SimpleFloatModel(1000.0)
        self.ground_offset_model = ui.SimpleFloatModel(0.0)

        self.clear_existing_model = ui.SimpleBoolModel(False)

        self.batch_size_model = ui.SimpleIntModel(25)
        self.preview_count_model = ui.SimpleIntModel(0)

        self.mesh_counts = {}
        self.list_frame = None
        self.progress_bar = None
        self.status_label = None

        self.is_processing = False
        self.cancel_requested = False

        self.build_ui()

    def set_status(self, text, progress=None):
        if self.status_label:
            self.status_label.text = text
        if self.progress_bar and progress is not None:
            self.progress_bar.model.set_value(max(0, min(1, float(progress))))
        print(text)

    def load_json(self):
        path = self.json_path_model.as_string.replace("\\", "/")
        if not os.path.exists(path):
            raise Exception(f"JSON file not found: {path}")
        with open(path, "r") as f:
            data = json.load(f)
        if not data:
            raise Exception("JSON is empty")
        return data

    def build_ui(self):
        with self.window.frame:
            with ui.ScrollingFrame():
                with ui.VStack(spacing=8):
                    ui.Label("UE Foliage JSON → Isaac Mapper", height=30)

                    ui.Label("JSON Path")
                    ui.StringField(model=self.json_path_model)

                    ui.Label("Output Root")
                    ui.StringField(model=self.output_root_model)

                    with ui.HStack(height=34):
                        ui.Button("Analyze JSON", clicked_fn=self.analyze_json)
                        ui.Button("Spawn Mapped Foliage", clicked_fn=self.spawn_clicked)
                        ui.Button("Cancel", clicked_fn=self.cancel_spawn)
                        ui.Button("Clear Mappings", clicked_fn=self.clear_mappings)

                    ui.Separator()
                    ui.Label("Transform Settings")

                    with ui.HStack(height=30):
                        ui.Label("Unit Scale", width=220)
                        ui.FloatField(model=self.unit_scale_model)

                    with ui.HStack(height=30):
                        ui.Label("Asset Scale Fix", width=220)
                        ui.FloatField(model=self.asset_scale_model)

                    with ui.HStack(height=30):
                        ui.Label("Flip Y Axis", width=220)
                        ui.CheckBox(model=self.flip_y_model)

                    with ui.HStack(height=30):
                        ui.Label("Yaw Offset", width=220)
                        ui.FloatField(model=self.yaw_offset_model)

                    ui.Separator()
                    ui.Label("Ground Snap Settings")

                    with ui.HStack(height=30):
                        ui.Label("Snap To Ground", width=220)
                        ui.CheckBox(model=self.snap_ground_model)

                    with ui.HStack(height=30):
                        ui.Label("Ray Start Height", width=220)
                        ui.FloatField(model=self.ray_start_height_model)

                    with ui.HStack(height=30):
                        ui.Label("Ray End Depth", width=220)
                        ui.FloatField(model=self.ray_end_depth_model)

                    with ui.HStack(height=30):
                        ui.Label("Ground Offset", width=220)
                        ui.FloatField(model=self.ground_offset_model)

                    ui.Separator()
                    ui.Label("Spawn Settings")

                    with ui.HStack(height=30):
                        ui.Label("Clear Existing Generated", width=220)
                        ui.CheckBox(model=self.clear_existing_model)

                    with ui.HStack(height=30):
                        ui.Label("Batch Size", width=220)
                        ui.IntField(model=self.batch_size_model)

                    with ui.HStack(height=30):
                        ui.Label("Preview Count, 0 = All", width=220)
                        ui.IntField(model=self.preview_count_model)

                    ui.Separator()
                    ui.Label("Item Types — select Isaac item, then click Add Selected")

                    self.list_frame = ui.Frame(height=430)
                    self.refresh_list()

                    ui.Separator()
                    ui.Label("Progress")
                    self.progress_bar = ui.ProgressBar(height=24)
                    self.progress_bar.model.set_value(0)
                    self.status_label = ui.Label("Idle", height=44)

    def refresh_list(self):
        def build():
            with ui.VStack(spacing=6):
                if not self.mesh_counts:
                    ui.Label("Click Analyze JSON first.", height=28)
                    return

                current_group = None

                sorted_items = sorted(
                    self.mesh_counts.items(),
                    key=lambda x: (base_group_name(x[0]), natural_key(x[0]))
                )

                for mesh_name, count in sorted_items:
                    group = base_group_name(mesh_name)

                    if group != current_group:
                        current_group = group
                        ui.Separator()
                        ui.Label(f"Group: {group}", height=24)

                    mapped = MESH_NAME_TO_ISAAC_PATH.get(mesh_name, "not mapped")

                    with ui.HStack(height=30):
                        ui.Label(f"{count}x", width=70)
                        ui.Label(mesh_name, width=260)
                        ui.Label(mapped, width=300)
                        ui.Button(
                            "Add Selected",
                            width=120,
                            clicked_fn=lambda m=mesh_name: self.add_selected_mapping(m)
                        )

        self.list_frame.set_build_fn(build)
        self.list_frame.rebuild()

    def analyze_json(self):
        try:
            data = self.load_json()
            counts = {}

            for item in data:
                name = extract_mesh_name(item.get("mesh", ""))
                counts[name] = counts.get(name, 0) + 1

            self.mesh_counts = counts
            self.refresh_list()

            self.set_status(f"Analyzed {len(data)} instances, {len(counts)} unique types.", 0)

        except Exception as e:
            self.set_status(f"Error: {e}", 0)
            print(e)

    def add_selected_mapping(self, mesh_name):
        selected = get_selected_prim_path()
        if not selected:
            self.set_status("Select an Isaac tree/item first.", 0)
            return

        prim = get_stage().GetPrimAtPath(selected)
        if not prim or not prim.IsValid():
            self.set_status("Invalid selected prim.", 0)
            return

        MESH_NAME_TO_ISAAC_PATH[mesh_name] = selected
        self.refresh_list()
        self.set_status(f"Mapped {mesh_name} → {selected}", 0)

    def clear_mappings(self):
        MESH_NAME_TO_ISAAC_PATH.clear()
        self.refresh_list()
        self.set_status("Mappings cleared.", 0)

    def cancel_spawn(self):
        self.cancel_requested = True
        self.set_status("Cancel requested...")

    def spawn_clicked(self):
        if self.is_processing:
            self.set_status("Already spawning.")
            return
        asyncio.ensure_future(self.spawn_async())

    async def spawn_async(self):
        self.is_processing = True
        self.cancel_requested = False

        try:
            stage = get_stage()
            data = self.load_json()

            preview = max(0, self.preview_count_model.as_int)
            if preview > 0:
                data = data[:preview]

            total = len(data)
            output_root = self.output_root_model.as_string

            if self.clear_existing_model.as_bool:
                clear_path(stage, output_root)

            ensure_xform(stage, output_root)

            unit_scale = self.unit_scale_model.as_float
            asset_scale = self.asset_scale_model.as_float
            flip_y = self.flip_y_model.as_bool
            yaw_offset = self.yaw_offset_model.as_float

            snap_ground = self.snap_ground_model.as_bool
            ray_start = self.ray_start_height_model.as_float
            ray_depth = self.ray_end_depth_model.as_float
            ground_offset = self.ground_offset_model.as_float

            batch_size = max(1, self.batch_size_model.as_int)

            spawned = 0
            skipped = 0
            failed = 0
            per_mesh = {}

            run_root = f"{output_root}/Run_{len(output_root)}_{int(asyncio.get_event_loop().time())}"
            ensure_xform(stage, run_root)

            for i, item in enumerate(data):
                if self.cancel_requested:
                    break

                mesh_name = extract_mesh_name(item.get("mesh", ""))
                source_path = MESH_NAME_TO_ISAAC_PATH.get(mesh_name)

                if not source_path:
                    skipped += 1
                    continue

                source_prim = stage.GetPrimAtPath(source_path)
                if not source_prim or not source_prim.IsValid():
                    failed += 1
                    continue

                mesh_root = f"{run_root}/{sanitize_name(mesh_name)}"
                ensure_xform(stage, mesh_root)

                idx = per_mesh.get(mesh_name, 0)
                per_mesh[mesh_name] = idx + 1

                target_path = f"{mesh_root}/{sanitize_name(mesh_name)}_{idx:05d}"

                try:
                    duplicate_prim(source_path, target_path)

                    ok = apply_transform(
                        target_path, item,
                        unit_scale, asset_scale,
                        flip_y, yaw_offset,
                        snap_ground, ray_start, ray_depth, ground_offset
                    )

                    if ok:
                        spawned += 1
                    else:
                        failed += 1

                except Exception as e:
                    failed += 1
                    print(f"Failed {mesh_name} index {i}: {e}")

                if (i + 1) % batch_size == 0 or i == total - 1:
                    self.set_status(
                        f"{i+1}/{total} | Spawned: {spawned} | Skipped: {skipped} | Failed: {failed}",
                        (i + 1) / total
                    )
                    await update_ui()

            self.set_status(
                f"Done. Spawned: {spawned}, Skipped: {skipped}, Failed: {failed}",
                1.0
            )

        except Exception as e:
            self.set_status(f"Error: {e}", 0)
            print(f"[Foliage Mapper Error] {e}")

        finally:
            self.is_processing = False


if "window_ref" in globals() and window_ref:
    try:
        window_ref.window.visible = False
    except Exception:
        pass

window_ref = FoliageWindow()