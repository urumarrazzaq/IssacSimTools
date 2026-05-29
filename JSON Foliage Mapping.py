import json
import os
import asyncio

import omni.usd
import omni.ui as ui
import omni.kit.app
import omni.kit.commands

from pxr import UsdGeom, Gf


WINDOW_TITLE = "UE Foliage JSON To Isaac Exact Spawner"
window_ref = None


def get_stage():
    return omni.usd.get_context().get_stage()


async def update_ui():
    await omni.kit.app.get_app().next_update_async()


def clear_path(stage, path):
    prim = stage.GetPrimAtPath(path)
    if prim and prim.IsValid():
        stage.RemovePrim(path)


def ensure_xform(stage, path):
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        UsdGeom.Xform.Define(stage, path)


def duplicate_prim(source_path, target_path):
    omni.kit.commands.execute(
        "CopyPrim",
        path_from=source_path,
        path_to=target_path
    )


def apply_ue_transform_to_isaac(
    target_path,
    item,
    unit_scale,
    asset_scale,
    flip_y,
    yaw_offset
):
    stage = get_stage()
    prim = stage.GetPrimAtPath(target_path)

    if not prim or not prim.IsValid():
        return False

    loc = item["location"]
    rot = item["rotation"]
    scale = item["scale"]

    # UE is usually centimeters.
    # Isaac/USD is usually meters.
    x = loc["x"] * unit_scale
    y = loc["y"] * unit_scale
    z = loc["z"] * unit_scale

    pitch = rot["pitch"]
    yaw = rot["yaw"] + yaw_offset
    roll = rot["roll"]

    # Optional mirror fix if Isaac import is mirrored compared to UE.
    if flip_y:
        y *= -1.0
        yaw *= -1.0
        roll *= -1.0

    # UE foliage instance scale from JSON.
    # asset_scale fixes the imported mesh size difference between UE and Isaac.
    sx = scale["x"] * asset_scale
    sy = scale["y"] * asset_scale
    sz = scale["z"] * asset_scale

    xf = UsdGeom.Xformable(prim)
    xf.ClearXformOpOrder()
    xf.SetResetXformStack(True)

    xf.AddTranslateOp().Set(Gf.Vec3d(x, y, z))

    # UE rotator exported as Pitch/Yaw/Roll.
    # USD RotateXYZ order uses X/Y/Z, so we map:
    # Roll -> X, Pitch -> Y, Yaw -> Z
    xf.AddRotateXYZOp().Set(Gf.Vec3f(roll, pitch, yaw))

    xf.AddScaleOp().Set(Gf.Vec3f(sx, sy, sz))

    return True


class FoliageSpawnerWindow:
    def __init__(self):
        self.window = ui.Window(WINDOW_TITLE, width=620, height=470)

        self.json_path_model = ui.SimpleStringModel(r"C:/Temp/foliage_export.json")
        self.source_tree_model = ui.SimpleStringModel("")
        self.output_root_model = ui.SimpleStringModel("/World/UE_Foliage_Meshes")

        self.unit_scale_model = ui.SimpleFloatModel(0.01)
        self.asset_scale_model = ui.SimpleFloatModel(0.01)

        self.flip_y_model = ui.SimpleBoolModel(False)
        self.yaw_offset_model = ui.SimpleFloatModel(0.0)
        self.batch_size_model = ui.SimpleIntModel(10)

        self.progress_bar = None
        self.status_label = None

        self.is_processing = False
        self.cancel_requested = False

        self.build_ui()

    def set_status(self, text, progress=None):
        if self.status_label:
            self.status_label.text = text

        if self.progress_bar and progress is not None:
            self.progress_bar.model.set_value(max(0.0, min(1.0, float(progress))))

        print(text)

    def build_ui(self):
        with self.window.frame:
            with ui.VStack(spacing=8):
                ui.Label("UE Foliage JSON → Isaac Exact Foliage Replication", height=28)

                ui.Label("UE Foliage JSON Path")
                ui.StringField(model=self.json_path_model)

                ui.Label("Isaac Source Tree Prim Path")
                ui.StringField(model=self.source_tree_model)

                with ui.HStack(height=32):
                    ui.Button("Get Selected Isaac Tree", clicked_fn=self.get_selected_tree)
                    ui.Button("Spawn Foliage From JSON", clicked_fn=self.spawn_clicked)
                    ui.Button("Cancel", clicked_fn=self.cancel_spawn)

                ui.Label("Output Root Path")
                ui.StringField(model=self.output_root_model)

                with ui.HStack(height=30):
                    ui.Label("Unit Scale UE cm → Isaac m", width=220)
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

                with ui.HStack(height=30):
                    ui.Label("Batch Size", width=220)
                    ui.IntField(model=self.batch_size_model)

                ui.Label(
                    "Recommended start: Unit Scale = 0.01, Asset Scale Fix = 0.01, Flip Y = OFF, Yaw Offset = 0. "
                    "If the forest is mirrored, turn Flip Y ON. If trees face wrong direction, try Yaw Offset 90, -90, or 180.",
                    word_wrap=True
                )

                ui.Separator()

                ui.Label("Progress")
                self.progress_bar = ui.ProgressBar(height=24)
                self.progress_bar.model.set_value(0.0)

                self.status_label = ui.Label("Idle", height=42)

    def get_selected_tree(self):
        selection = omni.usd.get_context().get_selection().get_selected_prim_paths()

        if not selection:
            self.set_status("Select the Isaac tree mesh first.", 0)
            return

        self.source_tree_model.set_value(selection[0])
        self.set_status(f"Selected source tree: {selection[0]}", 0)

    def cancel_spawn(self):
        self.cancel_requested = True
        self.set_status("Cancel requested...")

    def spawn_clicked(self):
        if self.is_processing:
            self.set_status("Already spawning. Please wait or press Cancel.")
            return

        asyncio.ensure_future(self.spawn_async())

    async def spawn_async(self):
        self.is_processing = True
        self.cancel_requested = False

        try:
            stage = get_stage()

            json_path = self.json_path_model.as_string.replace("\\", "/")
            source_tree_path = self.source_tree_model.as_string
            output_root = self.output_root_model.as_string

            unit_scale = self.unit_scale_model.as_float
            asset_scale = self.asset_scale_model.as_float
            flip_y = self.flip_y_model.as_bool
            yaw_offset = self.yaw_offset_model.as_float
            batch_size = max(1, self.batch_size_model.as_int)

            if not os.path.exists(json_path):
                raise Exception(f"JSON file not found: {json_path}")

            source_prim = stage.GetPrimAtPath(source_tree_path)
            if not source_prim or not source_prim.IsValid():
                raise Exception(f"Invalid Isaac source tree prim: {source_tree_path}")

            self.set_status("Loading UE foliage JSON...", 0)
            await update_ui()

            with open(json_path, "r") as f:
                data = json.load(f)

            if not data:
                raise Exception("JSON has no foliage instances.")

            total = len(data)

            self.set_status(f"Loaded {total} foliage instances. Clearing old output...", 0)
            await update_ui()

            clear_path(stage, output_root)
            ensure_xform(stage, output_root)

            await update_ui()

            spawned = 0
            failed = 0

            for i, item in enumerate(data):
                if self.cancel_requested:
                    self.set_status(
                        f"Cancelled at {i}/{total}. Spawned: {spawned}, Failed: {failed}",
                        i / total
                    )
                    break

                target_path = f"{output_root}/Tree_{i:05d}"

                try:
                    duplicate_prim(source_tree_path, target_path)

                    ok = apply_ue_transform_to_isaac(
                        target_path=target_path,
                        item=item,
                        unit_scale=unit_scale,
                        asset_scale=asset_scale,
                        flip_y=flip_y,
                        yaw_offset=yaw_offset
                    )

                    if ok:
                        spawned += 1
                    else:
                        failed += 1

                except Exception as e:
                    failed += 1
                    print(f"Failed foliage {i}: {e}")

                if (i + 1) % batch_size == 0 or i == total - 1:
                    progress = (i + 1) / total
                    self.set_status(
                        f"Spawning foliage... {i + 1}/{total} | Spawned: {spawned} | Failed: {failed}",
                        progress
                    )
                    await update_ui()

            if not self.cancel_requested:
                self.set_status(
                    f"Completed. Spawned: {spawned}/{total}, Failed: {failed}",
                    1.0
                )

        except Exception as e:
            self.set_status(f"Error: {e}", 0)
            print(f"[UE → Isaac Foliage Spawner Error] {e}")

        finally:
            self.is_processing = False


if "window_ref" in globals() and window_ref:
    try:
        window_ref.window.visible = False
    except Exception:
        pass

window_ref = FoliageSpawnerWindow()