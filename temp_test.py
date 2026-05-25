import json
import os
import asyncio

import omni.usd
import omni.ui as ui
import omni.kit.app
import omni.kit.commands

from pxr import UsdGeom, Gf


WINDOW_TITLE = "UE Foliage JSON Async Spawner"

window_ref = None


def refresh_stage():
    return omni.usd.get_context().get_stage()


async def yield_ui():
    await omni.kit.app.get_app().next_update_async()


def clear_path(stage, path):
    prim = stage.GetPrimAtPath(path)
    if prim and prim.IsValid():
        stage.RemovePrim(path)


def ensure_xform(stage, path):
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        UsdGeom.Xform.Define(stage, path)


def duplicate_tree(source_path, target_path):
    omni.kit.commands.execute(
        "CopyPrim",
        path_from=source_path,
        path_to=target_path
    )


def apply_transform(path, item, unit_scale, extra_scale, flip_y):
    stage = refresh_stage()
    prim = stage.GetPrimAtPath(path)

    if not prim or not prim.IsValid():
        return False

    loc = item["location"]
    rot = item["rotation"]
    scale = item["scale"]

    x = loc["x"] * unit_scale
    y = loc["y"] * unit_scale
    z = loc["z"] * unit_scale

    if flip_y:
        y *= -1.0

    sx = scale["x"] * extra_scale
    sy = scale["y"] * extra_scale
    sz = scale["z"] * extra_scale

    pitch = rot["pitch"]
    yaw = rot["yaw"]
    roll = rot["roll"]

    xf = UsdGeom.Xformable(prim)
    xf.ClearXformOpOrder()
    xf.SetResetXformStack(True)

    xf.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
    xf.AddRotateXYZOp().Set(Gf.Vec3f(roll, pitch, yaw))
    xf.AddScaleOp().Set(Gf.Vec3f(sx, sy, sz))

    return True


class FoliageSpawnerWindow:
    def __init__(self):
        self.window = ui.Window(WINDOW_TITLE, width=560, height=430)

        self.json_path_model = ui.SimpleStringModel(r"C:/Temp/foliage_export.json")
        self.tree_path_model = ui.SimpleStringModel("")
        self.output_path_model = ui.SimpleStringModel("/World/UE_Foliage_Meshes")

        self.unit_scale_model = ui.SimpleFloatModel(0.01)
        self.extra_scale_model = ui.SimpleFloatModel(1.0)
        self.flip_y_model = ui.SimpleBoolModel(False)
        self.batch_size_model = ui.SimpleIntModel(10)

        self.status_label = None
        self.progress_bar = None

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
                ui.Label("UE Foliage JSON → Non Blocking Async Spawner", height=28)

                ui.Label("JSON Path")
                ui.StringField(model=self.json_path_model)

                ui.Label("Selected Tree Prim Path")
                ui.StringField(model=self.tree_path_model)

                with ui.HStack(height=32):
                    ui.Button("Get Selected Tree", clicked_fn=self.get_selected_tree)
                    ui.Button("Spawn All JSON Trees", clicked_fn=self.spawn_clicked)
                    ui.Button("Cancel", clicked_fn=self.cancel_spawn)

                ui.Label("Output Root Path")
                ui.StringField(model=self.output_path_model)

                with ui.HStack(height=30):
                    ui.Label("Unit Scale", width=160)
                    ui.FloatField(model=self.unit_scale_model)

                with ui.HStack(height=30):
                    ui.Label("Extra Tree Scale", width=160)
                    ui.FloatField(model=self.extra_scale_model)

                with ui.HStack(height=30):
                    ui.Label("Flip Y Axis", width=160)
                    ui.CheckBox(model=self.flip_y_model)

                with ui.HStack(height=30):
                    ui.Label("Batch Size", width=160)
                    ui.IntField(model=self.batch_size_model)

                ui.Label(
                    "Batch Size = how many trees spawn before Isaac updates UI. "
                    "Use 5–25 if the editor feels heavy.",
                    word_wrap=True
                )

                ui.Separator()

                ui.Label("Progress")
                self.progress_bar = ui.ProgressBar(height=24)
                self.progress_bar.model.set_value(0.0)

                self.status_label = ui.Label("Idle", height=40)

    def get_selected_tree(self):
        selection = omni.usd.get_context().get_selection().get_selected_prim_paths()

        if not selection:
            self.set_status("Select one tree mesh first.", 0)
            return

        self.tree_path_model.set_value(selection[0])
        self.set_status(f"Selected tree: {selection[0]}", 0)

    def cancel_spawn(self):
        self.cancel_requested = True
        self.set_status("Cancel requested... waiting for current batch to finish.")

    def spawn_clicked(self):
        if self.is_processing:
            self.set_status("Already spawning. Press Cancel or wait.")
            return

        asyncio.ensure_future(self.spawn_async())

    async def spawn_async(self):
        self.is_processing = True
        self.cancel_requested = False

        try:
            stage = refresh_stage()

            json_path = self.json_path_model.as_string.replace("\\", "/")
            source_tree_path = self.tree_path_model.as_string
            output_root = self.output_path_model.as_string

            unit_scale = self.unit_scale_model.as_float
            extra_scale = self.extra_scale_model.as_float
            flip_y = self.flip_y_model.as_bool
            batch_size = max(1, self.batch_size_model.as_int)

            if extra_scale <= 0:
                raise Exception("Extra Tree Scale must be greater than 0. Use 1.0.")

            if not os.path.exists(json_path):
                raise Exception(f"JSON file not found: {json_path}")

            source_prim = stage.GetPrimAtPath(source_tree_path)
            if not source_prim or not source_prim.IsValid():
                raise Exception(f"Invalid selected tree prim: {source_tree_path}")

            self.set_status("Loading JSON...", 0)
            await yield_ui()

            with open(json_path, "r") as f:
                data = json.load(f)

            total = len(data)

            if total <= 0:
                raise Exception("JSON has no foliage instances.")

            self.set_status(f"Loaded {total} trees. Clearing old output...", 0)
            await yield_ui()

            clear_path(stage, output_root)
            ensure_xform(stage, output_root)

            await yield_ui()

            spawned = 0
            failed = 0

            for i, item in enumerate(data):
                if self.cancel_requested:
                    self.set_status(f"Cancelled at {i}/{total}. Spawned: {spawned}, Failed: {failed}", i / total)
                    break

                target_path = f"{output_root}/Tree_{i:05d}"

                try:
                    duplicate_tree(source_tree_path, target_path)

                    ok = apply_transform(
                        target_path,
                        item,
                        unit_scale,
                        extra_scale,
                        flip_y
                    )

                    if ok:
                        spawned += 1
                    else:
                        failed += 1

                except Exception as e:
                    failed += 1
                    print(f"Failed tree {i}: {e}")

                if i % batch_size == 0:
                    progress = (i + 1) / total
                    self.set_status(
                        f"Spawning trees... {i + 1}/{total} | Spawned: {spawned} | Failed: {failed}",
                        progress
                    )
                    await yield_ui()

            if not self.cancel_requested:
                self.set_status(
                    f"Completed. Spawned: {spawned}/{total}, Failed: {failed}",
                    1.0
                )

        except Exception as e:
            self.set_status(f"Error: {e}", 0)
            print(f"[UE Foliage Async Spawner Error] {e}")

        finally:
            self.is_processing = False


if "window_ref" in globals() and window_ref:
    try:
        window_ref.window.visible = False
    except Exception:
        pass

window_ref = FoliageSpawnerWindow()