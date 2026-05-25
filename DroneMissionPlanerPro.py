import omni.usd
import omni.ui as ui
import omni.kit.app
from pxr import Usd, UsdGeom, Gf
import heapq, random, json, os, math

stage = omni.usd.get_context().get_stage()
selection = omni.usd.get_context().get_selection()

window = None
update_sub = None

PATH_ROOT = "/World/GeneratedDronePath"
NAV_GRID_ROOT = "/World/GeneratedNavGrid"
MARKER_ROOT = "/World/PathMarkers"
DEBUG_ROOT = "/World/ObstacleDebugBoxes"
VISUAL_ROOT = "/World/PathVisuals"
GHOST_ROOT = "/World/GhostDronePreview"

ignored_paths = set()
start_marker_path = None
end_marker_path = None
current_points = []
ui_settings = {}

ghost_index = 0.0
ghost_playing = False
ghost_speed_value = 1.0
ghost_camera_mode = "Disabled"
ghost_camera_distance = 6.0
ghost_camera_height = 3.0
ghost_camera_path = "/World/GhostPreviewCamera"

start_label = None
end_label = None
ignore_label = None
stats_label = None

COLORS = {
    "Red": Gf.Vec3f(1, 0, 0),
    "Green": Gf.Vec3f(0, 1, 0),
    "Blue": Gf.Vec3f(0, 0, 1),
    "Yellow": Gf.Vec3f(1, 1, 0),
    "Cyan": Gf.Vec3f(0, 1, 1),
    "Purple": Gf.Vec3f(0.7, 0, 1),
    "White": Gf.Vec3f(1, 1, 1),
    "Orange": Gf.Vec3f(1, 0.45, 0),
}

COLOR_OPTIONS = list(COLORS.keys())
PATH_MODES = ["Shortest", "Random", "Smooth Cinematic", "Wide Turns", "Aggressive", "Exploration", "Patrol"]
VISUAL_MODES = ["Points", "Bezier Spline", "Catmull-Rom Spline", "Ribbon Mesh", "Tube Mesh"]
CAMERA_MODES = ["Disabled", "Follow", "FPV", "Orbit", "Top Down"]


def float_input(label, default):
    with ui.HStack(height=28):
        ui.Label(label, width=190)
        field = ui.FloatField(width=120)
        field.model.set_value(default)
    return field


def string_input(label, default):
    ui.Label(label)
    field = ui.StringField(height=28)
    field.model.set_value(default)
    return field


def combo_value(combo, options):
    idx = combo.model.get_item_value_model().as_int
    return options[idx]


def refresh_labels():
    if start_label:
        start_label.text = f"Start: {start_marker_path}" if start_marker_path else "Start: None"
    if end_label:
        end_label.text = f"End: {end_marker_path}" if end_marker_path else "End: None"
    if ignore_label:
        ignore_label.text = "Ignored: None" if not ignored_paths else "Ignored:\n" + "\n".join(sorted(ignored_paths))


def get_world_pos(prim):
    xform = UsdGeom.Xformable(prim)
    pos = xform.ComputeLocalToWorldTransform(0).ExtractTranslation()
    return Gf.Vec3d(pos[0], pos[1], pos[2])


def get_world_bbox(prim):
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_], useExtentsHint=True)
    box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
    return box.GetMin(), box.GetMax()


def clear_prim(path):
    if stage.GetPrimAtPath(path):
        stage.RemovePrim(path)


def create_cube(path, x, y, z, size, color, height=0.04):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.AddTranslateOp().Set(Gf.Vec3f(x, y, z))
    cube.AddScaleOp().Set(Gf.Vec3f(size, size, height))
    cube.GetDisplayColorAttr().Set([color])


def clear_path():
    clear_prim(PATH_ROOT)
    clear_prim(VISUAL_ROOT)
    print("Cleared Path")


def clear_nav_grid():
    clear_prim(NAV_GRID_ROOT)
    print("Cleared Nav Grid")


def clear_debug():
    clear_prim(DEBUG_ROOT)
    print("Cleared Obstacle Debug Boxes")


def clear_ghost():
    global ghost_playing, ghost_index
    ghost_playing = False
    ghost_index = 0.0
    clear_prim(GHOST_ROOT)
    print("Cleared Ghost")


def clear_markers():
    global start_marker_path, end_marker_path
    clear_prim(MARKER_ROOT)
    start_marker_path = None
    end_marker_path = None
    refresh_labels()
    print("Cleared Markers")


def clear_ignore():
    ignored_paths.clear()
    refresh_labels()
    print("Cleared Ignore List")


def create_marker(name, x, y, z, color):
    if not stage.GetPrimAtPath(MARKER_ROOT):
        UsdGeom.Xform.Define(stage, MARKER_ROOT)

    path = f"{MARKER_ROOT}/{name}"
    clear_prim(path)

    sphere = UsdGeom.Sphere.Define(stage, path)
    sphere.AddTranslateOp().Set(Gf.Vec3f(x, y, z))
    sphere.GetRadiusAttr().Set(0.45)
    sphere.GetDisplayColorAttr().Set([color])

    selection.set_selected_prim_paths([path], True)
    return path


def create_start_marker(x, y, z):
    global start_marker_path
    start_marker_path = create_marker("StartPoint", x, y, z, Gf.Vec3f(0, 1, 0))
    refresh_labels()
    print("Created Start Marker")


def create_end_marker(x, y, z):
    global end_marker_path
    end_marker_path = create_marker("EndPoint", x, y, z, Gf.Vec3f(1, 0, 0))
    refresh_labels()
    print("Created End Marker")


def set_selected_as_start():
    global start_marker_path
    selected = selection.get_selected_prim_paths()
    if selected:
        start_marker_path = str(selected[0])
    refresh_labels()


def set_selected_as_end():
    global end_marker_path
    selected = selection.get_selected_prim_paths()
    if selected:
        end_marker_path = str(selected[0])
    refresh_labels()


def add_selected_to_ignore():
    selected = selection.get_selected_prim_paths()
    for p in selected:
        ignored_paths.add(str(p))
    refresh_labels()


def is_ignored(prim):
    path = str(prim.GetPath())
    return any(path.startswith(i) for i in ignored_paths)


def is_blocked(x, y, obstacles):
    for min_x, max_x, min_y, max_y in obstacles:
        if min_x <= x <= max_x and min_y <= y <= max_y:
            return True
    return False


def collect_obstacles(padding, show_debug):
    obstacles = []
    clear_debug()

    if show_debug:
        UsdGeom.Xform.Define(stage, DEBUG_ROOT)

    for prim in stage.Traverse():
        path = str(prim.GetPath())

        if path.startswith(PATH_ROOT): continue
        if path.startswith(NAV_GRID_ROOT): continue
        if path.startswith(MARKER_ROOT): continue
        if path.startswith(DEBUG_ROOT): continue
        if path.startswith(VISUAL_ROOT): continue
        if path.startswith(GHOST_ROOT): continue
        if path == start_marker_path or path == end_marker_path: continue
        if is_ignored(prim): continue

        if prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Cube) or prim.IsA(UsdGeom.Capsule):
            try:
                mn, mx = get_world_bbox(prim)

                min_x = mn[0] - padding
                max_x = mx[0] + padding
                min_y = mn[1] - padding
                max_y = mx[1] + padding

                obstacles.append((min_x, max_x, min_y, max_y))

                if show_debug:
                    cx = (min_x + max_x) * 0.5
                    cy = (min_y + max_y) * 0.5
                    sx = max_x - min_x
                    sy = max_y - min_y

                    cube = UsdGeom.Cube.Define(stage, f"{DEBUG_ROOT}/ObstacleBox_{len(obstacles)}")
                    cube.AddTranslateOp().Set(Gf.Vec3f(cx, cy, 0.15))
                    cube.AddScaleOp().Set(Gf.Vec3f(sx * 0.5, sy * 0.5, 0.05))
                    cube.GetDisplayColorAttr().Set([Gf.Vec3f(1, 0.45, 0)])
            except Exception as e:
                print("Obstacle Failed:", path, e)

    print("Total Obstacles:", len(obstacles))
    return obstacles


def world_to_grid(x, y, min_x, min_y, step):
    return int(round((x - min_x) / step)), int(round((y - min_y) / step))


def grid_to_world(cell, min_x, min_y, step):
    return min_x + cell[0] * step, min_y + cell[1] * step


def heuristic(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def get_mode_settings(mode):
    if mode == "Shortest": return 0.0, 0.0
    if mode == "Random": return 5.0, 1.0
    if mode == "Smooth Cinematic": return 2.0, 3.0
    if mode == "Wide Turns": return 1.0, 6.0
    if mode == "Aggressive": return 8.0, 0.0
    if mode == "Exploration": return 12.0, 2.0
    if mode == "Patrol": return 3.0, 3.0
    return 5.0, 1.0


def astar(start, goal, min_x, max_x, min_y, max_y, step, obstacles, randomness, turn_bias):
    open_set = [(0, start)]
    came_from = {}
    g_score = {start: 0}

    directions = [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        random.shuffle(directions)

        for dx, dy in directions:
            nx = current[0] + dx
            ny = current[1] + dy
            wx = min_x + nx * step
            wy = min_y + ny * step

            if wx < min_x or wx > max_x: continue
            if wy < min_y or wy > max_y: continue
            if is_blocked(wx, wy, obstacles): continue

            neighbor = (nx, ny)
            move_cost = 1.4 if dx != 0 and dy != 0 else 1.0
            random_cost = random.uniform(0, randomness)

            turn_cost = 0.0
            if current in came_from:
                prev = came_from[current]
                old_dx = current[0] - prev[0]
                old_dy = current[1] - prev[1]
                if old_dx != dx or old_dy != dy:
                    turn_cost = turn_bias

            new_g = g_score[current] + move_cost + random_cost + turn_cost

            if neighbor not in g_score or new_g < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = new_g
                f = new_g + heuristic(neighbor, goal) + random.uniform(0, randomness)
                heapq.heappush(open_set, (f, neighbor))

    return []


def smooth_catmull(points, subdivisions=4):
    if len(points) < 4:
        return points

    result = []

    for i in range(len(points) - 1):
        p0 = points[max(i - 1, 0)]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[min(i + 2, len(points) - 1)]

        for j in range(subdivisions):
            t = j / float(subdivisions)
            t2 = t * t
            t3 = t2 * t

            x = 0.5 * ((2*p1[0]) + (-p0[0]+p2[0])*t + (2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2 + (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3)
            y = 0.5 * ((2*p1[1]) + (-p0[1]+p2[1])*t + (2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2 + (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)
            z = 0.5 * ((2*p1[2]) + (-p0[2]+p2[2])*t + (2*p0[2]-5*p1[2]+4*p2[2]-p3[2])*t2 + (-p0[2]+3*p1[2]-3*p2[2]+p3[2])*t3)

            result.append((x, y, z))

    result.append(points[-1])
    return result


def draw_points(points, size, color):
    clear_prim(PATH_ROOT)
    UsdGeom.Xform.Define(stage, PATH_ROOT)

    for i, p in enumerate(points):
        create_cube(f"{PATH_ROOT}/Point_{i}", p[0], p[1], p[2], size, color)


def draw_curve(points, color, width=0.08):
    clear_prim(VISUAL_ROOT)
    UsdGeom.Xform.Define(stage, VISUAL_ROOT)

    curve = UsdGeom.BasisCurves.Define(stage, f"{VISUAL_ROOT}/Curve")
    curve.CreateTypeAttr("linear")
    curve.CreateCurveVertexCountsAttr([len(points)])
    curve.CreatePointsAttr([Gf.Vec3f(p[0], p[1], p[2]) for p in points])
    curve.CreateWidthsAttr([width])
    curve.GetDisplayColorAttr().Set([color])


def draw_visual(points, visual_mode, color):
    clear_prim(VISUAL_ROOT)

    if visual_mode == "Points":
        return

    smooth = smooth_catmull(points, 4)

    if visual_mode == "Bezier Spline":
        draw_curve(smooth, color, 0.08)
    elif visual_mode == "Catmull-Rom Spline":
        draw_curve(smooth, color, 0.08)
    elif visual_mode == "Ribbon Mesh":
        draw_curve(smooth, color, 0.18)
    elif visual_mode == "Tube Mesh":
        draw_curve(smooth, color, 0.3)


def path_length(points):
    total = 0.0
    for i in range(1, len(points)):
        a = points[i - 1]
        b = points[i]
        total += math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)
    return total


def generate_nav_grid(area_min_x, area_max_x, area_min_y, area_max_y, spacing, padding, height, size, color_name, show_debug):
    if spacing <= 0:
        print("Grid spacing must be greater than 0.")
        return

    clear_nav_grid()
    obstacles = collect_obstacles(padding, show_debug)
    color = COLORS.get(color_name, COLORS["Green"])

    UsdGeom.Xform.Define(stage, NAV_GRID_ROOT)

    count = 0
    x = area_min_x

    while x <= area_max_x:
        y = area_min_y
        while y <= area_max_y:
            if not is_blocked(x, y, obstacles):
                create_cube(f"{NAV_GRID_ROOT}/Cell_{count}", x, y, height, size, color, 0.025)
                count += 1
            y += spacing
        x += spacing

    print("Generated Nav Grid:", count)


def generate_path(area_min_x, area_max_x, area_min_y, area_max_y, spacing, padding, height, point_size, mode, visual_mode, color_name, show_debug, project_file, settings=None):
    global current_points

    if not start_marker_path or not end_marker_path:
        print("Set Start and End First")
        return []

    if spacing <= 0:
        print("Path spacing must be greater than 0.")
        return []

    start_prim = stage.GetPrimAtPath(start_marker_path)
    end_prim = stage.GetPrimAtPath(end_marker_path)

    if not start_prim.IsValid() or not end_prim.IsValid():
        print("Start or End marker invalid.")
        return []

    randomness, turn_bias = get_mode_settings(mode)
    obstacles = collect_obstacles(padding, show_debug)

    sp = get_world_pos(start_prim)
    ep = get_world_pos(end_prim)

    start = world_to_grid(sp[0], sp[1], area_min_x, area_min_y, spacing)
    goal = world_to_grid(ep[0], ep[1], area_min_x, area_min_y, spacing)

    cells = astar(start, goal, area_min_x, area_max_x, area_min_y, area_max_y, spacing, obstacles, randomness, turn_bias)

    if not cells:
        print("Failed To Generate Path")
        return []

    points = []

    for c in cells:
        x, y = grid_to_world(c, area_min_x, area_min_y, spacing)
        points.append((x, y, height))

    if mode in ["Smooth Cinematic", "Wide Turns", "Patrol"]:
        points = smooth_catmull(points, 5)

    color = COLORS.get(color_name, COLORS["Red"])

    draw_points(points, point_size, color)
    draw_visual(points, visual_mode, color)

    current_points = points
    length = path_length(points)

    if stats_label:
        stats_label.text = f"Points={len(points)} | Length={round(length,2)} | EstTime={round(length/2.0,2)}s"

    save_project(project_file, settings)
    print("Generated Path")
    return points


def create_ghost(color_name="Cyan"):
    clear_ghost()

    if not current_points:
        print("No path for ghost.")
        return

    color = COLORS.get(color_name, COLORS["Cyan"])
    UsdGeom.Xform.Define(stage, GHOST_ROOT)

    ghost = UsdGeom.Sphere.Define(stage, f"{GHOST_ROOT}/GhostDrone")
    p = current_points[0]
    ghost.AddTranslateOp().Set(Gf.Vec3f(p[0], p[1], p[2] + 0.4))
    ghost.GetRadiusAttr().Set(0.35)
    ghost.GetDisplayColorAttr().Set([color])


def set_ghost_position(pos):
    prim = stage.GetPrimAtPath(f"{GHOST_ROOT}/GhostDrone")
    if not prim.IsValid():
        return

    xform = UsdGeom.Xformable(prim)
    ops = xform.GetOrderedXformOps()
    translate = ops[0] if ops else xform.AddTranslateOp()
    translate.Set(Gf.Vec3f(pos[0], pos[1], pos[2] + 0.4))


def update_ghost_camera(pos, index):

    if not stage.GetPrimAtPath(ghost_camera_path):

        camera = UsdGeom.Camera.Define(
            stage,
            ghost_camera_path
        )

        camera.AddTranslateOp()
        camera.AddRotateXYZOp()

    cam_prim = stage.GetPrimAtPath(
        ghost_camera_path
    )

    xform = UsdGeom.Xformable(cam_prim)

    ops = xform.GetOrderedXformOps()

    translate_op = ops[0]
    rotate_op = ops[1]

    distance = ghost_camera_distance
    height = ghost_camera_height

    if ghost_camera_mode == "Follow":

        cam_pos = Gf.Vec3f(
            pos[0] - distance,
            pos[1] - distance,
            pos[2] + height
        )

        cam_rot = Gf.Vec3f(
            60,
            0,
            -45
        )

    elif ghost_camera_mode == "FPV":

        cam_pos = Gf.Vec3f(
            pos[0],
            pos[1],
            pos[2] + 0.5
        )

        cam_rot = Gf.Vec3f(
            75,
            0,
            0
        )

    elif ghost_camera_mode == "Top Down":

        cam_pos = Gf.Vec3f(
            pos[0],
            pos[1],
            pos[2] + (height * 4)
        )

        cam_rot = Gf.Vec3f(
            0,
            0,
            0
        )

    elif ghost_camera_mode == "Orbit":

        angle = index * 0.08

        cam_pos = Gf.Vec3f(
            pos[0] + math.cos(angle) * distance,
            pos[1] + math.sin(angle) * distance,
            pos[2] + height
        )

        cam_rot = Gf.Vec3f(
            60,
            0,
            math.degrees(angle) + 90
        )

    else:
        return

    translate_op.Set(cam_pos)
    rotate_op.Set(cam_rot)

    
def start_ghost_preview():
    global ghost_playing, ghost_index

    if not current_points:
        print("Generate Path First")
        return

    create_ghost()
    ghost_index = 0.0
    ghost_playing = True
    print("Ghost Preview Started")


def stop_ghost_preview():
    global ghost_playing
    ghost_playing = False
    print("Ghost Preview Stopped")


def update_ghost(event):
    global ghost_index

    if not ghost_playing or not current_points:
        return

    ghost_index += ghost_speed_value

    if ghost_index >= len(current_points):
        ghost_index = 0.0

    index = int(ghost_index)
    pos = current_points[index]

    set_ghost_position(pos)

    if ghost_camera_mode != "Disabled":
        update_ghost_camera(pos, index)


def collect_ui_settings(area_min_x, area_max_x, area_min_y, area_max_y,
                        grid_spacing, grid_padding, grid_height, grid_size, grid_color, grid_debug,
                        path_spacing, path_padding, path_height, path_point_size, path_mode, visual_mode, path_color, path_debug,
                        ghost_speed_field, camera_mode_combo):

    return {
        "area": {
            "min_x": area_min_x.model.get_value_as_float(),
            "max_x": area_max_x.model.get_value_as_float(),
            "min_y": area_min_y.model.get_value_as_float(),
            "max_y": area_max_y.model.get_value_as_float(),
        },
        "grid": {
            "spacing": grid_spacing.model.get_value_as_float(),
            "padding": grid_padding.model.get_value_as_float(),
            "height": grid_height.model.get_value_as_float(),
            "size": grid_size.model.get_value_as_float(),
            "color": combo_value(grid_color, COLOR_OPTIONS),
            "debug": grid_debug.get_value_as_bool(),
        },
        "path": {
            "spacing": path_spacing.model.get_value_as_float(),
            "padding": path_padding.model.get_value_as_float(),
            "height": path_height.model.get_value_as_float(),
            "point_size": path_point_size.model.get_value_as_float(),
            "mode": combo_value(path_mode, PATH_MODES),
            "visual_mode": combo_value(visual_mode, VISUAL_MODES),
            "color": combo_value(path_color, COLOR_OPTIONS),
            "debug": path_debug.get_value_as_bool(),
        },
        "ghost": {
            "speed": ghost_speed_field.model.get_value_as_float(),
            "camera_mode": combo_value(camera_mode_combo, CAMERA_MODES),
        }
    }


def save_project(file_path, settings=None):
    try:
        folder = os.path.dirname(file_path)
        if folder:
            os.makedirs(folder, exist_ok=True)

        data = {
            "settings": settings if settings else ui_settings,
            "start_marker_path": start_marker_path,
            "end_marker_path": end_marker_path,
            "ignored_paths": list(ignored_paths),
            "points": [{"x": p[0], "y": p[1], "z": p[2]} for p in current_points],
        }

        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)

        print("Project Saved With Settings:", file_path)
    except Exception as e:
        print("Save Failed:", e)


def load_project(file_path, color_name, point_size):
    global start_marker_path, end_marker_path, ignored_paths, current_points, ui_settings

    if not os.path.exists(file_path):
        print("Project File Missing")
        return

    with open(file_path, "r") as f:
        data = json.load(f)

    ui_settings = data.get("settings", {})
    start_marker_path = data.get("start_marker_path")
    end_marker_path = data.get("end_marker_path")
    ignored_paths = set(data.get("ignored_paths", []))
    current_points = [(p["x"], p["y"], p["z"]) for p in data.get("points", [])]

    color = COLORS.get(color_name, COLORS["Red"])

    if current_points:
        draw_points(current_points, point_size, color)

    refresh_labels()
    print("Project Loaded")


def create_gui():
    global window, start_label, end_label, ignore_label, stats_label, update_sub
    global ui_settings, ghost_speed_value, ghost_camera_mode

    update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(update_ghost)

    window = ui.Window("Drone Mission Planner Pro", width=600, height=850)

    with window.frame:
        with ui.ScrollingFrame(
            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
        ):
            with ui.VStack(spacing=8, height=0):

                with ui.CollapsableFrame("Area Bounds", collapsed=False):
                    with ui.VStack(spacing=8):
                        area_min_x = float_input("Area Min X", -20)
                        area_max_x = float_input("Area Max X", 20)
                        area_min_y = float_input("Area Min Y", -20)
                        area_max_y = float_input("Area Max Y", 20)

                with ui.CollapsableFrame("Nav Grid Settings", collapsed=False):
                    with ui.VStack(spacing=8):
                        grid_spacing = float_input("Grid Spacing", 1.0)
                        grid_padding = float_input("Grid Obstacle Padding", 0.8)
                        grid_height = float_input("Grid Height", 0.25)
                        grid_size = float_input("Grid Cell Size", 0.2)

                        ui.Label("Grid Color")
                        grid_color = ui.ComboBox(1, *COLOR_OPTIONS)

                        grid_debug = ui.SimpleBoolModel(False)
                        with ui.HStack(height=28):
                            ui.CheckBox(grid_debug)
                            ui.Label("Show Grid Obstacle Debug Boxes")

                        with ui.HStack(height=32):
                            ui.Button("Generate Nav Grid", clicked_fn=lambda: generate_nav_grid(
                                area_min_x.model.get_value_as_float(),
                                area_max_x.model.get_value_as_float(),
                                area_min_y.model.get_value_as_float(),
                                area_max_y.model.get_value_as_float(),
                                grid_spacing.model.get_value_as_float(),
                                grid_padding.model.get_value_as_float(),
                                grid_height.model.get_value_as_float(),
                                grid_size.model.get_value_as_float(),
                                combo_value(grid_color, COLOR_OPTIONS),
                                grid_debug.get_value_as_bool()
                            ))
                            ui.Button("Clear Grid", clicked_fn=clear_nav_grid)

                with ui.CollapsableFrame("Path Settings", collapsed=False):
                    with ui.VStack(spacing=8):
                        path_spacing = float_input("Path Spacing", 1.0)
                        path_padding = float_input("Path Obstacle Padding", 0.8)
                        path_height = float_input("Path Height", 1.5)
                        path_point_size = float_input("Path Point Size", 0.25)

                        ui.Label("Path Mode")
                        path_mode = ui.ComboBox(0, *PATH_MODES)

                        ui.Label("Visual Mode")
                        visual_mode = ui.ComboBox(0, *VISUAL_MODES)

                        ui.Label("Path Color")
                        path_color = ui.ComboBox(0, *COLOR_OPTIONS)

                        path_debug = ui.SimpleBoolModel(False)
                        with ui.HStack(height=28):
                            ui.CheckBox(path_debug)
                            ui.Label("Show Path Obstacle Debug Boxes")

                        def run_generate_path():
                            global ui_settings

                            ui_settings = collect_ui_settings(
                                area_min_x, area_max_x, area_min_y, area_max_y,
                                grid_spacing, grid_padding, grid_height, grid_size, grid_color, grid_debug,
                                path_spacing, path_padding, path_height, path_point_size, path_mode, visual_mode, path_color, path_debug,
                                ghost_speed_field, camera_mode_combo
                            )

                            generate_path(
                                area_min_x.model.get_value_as_float(),
                                area_max_x.model.get_value_as_float(),
                                area_min_y.model.get_value_as_float(),
                                area_max_y.model.get_value_as_float(),
                                path_spacing.model.get_value_as_float(),
                                path_padding.model.get_value_as_float(),
                                path_height.model.get_value_as_float(),
                                path_point_size.model.get_value_as_float(),
                                combo_value(path_mode, PATH_MODES),
                                combo_value(visual_mode, VISUAL_MODES),
                                combo_value(path_color, COLOR_OPTIONS),
                                path_debug.get_value_as_bool(),
                                project_file.model.get_value_as_string(),
                                ui_settings
                            )

                        ui.Button("Generate Path", clicked_fn=run_generate_path)
                        stats_label = ui.Label("Stats: No Path", height=45)

                with ui.CollapsableFrame("Markers / Start End", collapsed=False):
                    with ui.VStack(spacing=8):
                        start_x = float_input("Start X", -18)
                        start_y = float_input("Start Y", -18)
                        start_z = float_input("Start Z", 0.5)

                        end_x = float_input("End X", 18)
                        end_y = float_input("End Y", 18)
                        end_z = float_input("End Z", 0.5)

                        with ui.HStack(height=32):
                            ui.Button("Create Start", clicked_fn=lambda: create_start_marker(
                                start_x.model.get_value_as_float(),
                                start_y.model.get_value_as_float(),
                                start_z.model.get_value_as_float()
                            ))
                            ui.Button("Create End", clicked_fn=lambda: create_end_marker(
                                end_x.model.get_value_as_float(),
                                end_y.model.get_value_as_float(),
                                end_z.model.get_value_as_float()
                            ))

                        start_label = ui.Label("Start: None", height=40)
                        end_label = ui.Label("End: None", height=40)

                        with ui.HStack(height=32):
                            ui.Button("Set Selected Start", clicked_fn=set_selected_as_start)
                            ui.Button("Set Selected End", clicked_fn=set_selected_as_end)

                with ui.CollapsableFrame("Ignore Objects", collapsed=False):
                    with ui.VStack(spacing=8):
                        with ui.HStack(height=32):
                            ui.Button("Add Selected To Ignore", clicked_fn=add_selected_to_ignore)
                            ui.Button("Clear Ignore", clicked_fn=clear_ignore)

                        ignore_label = ui.Label("Ignored: None", height=120)

                with ui.CollapsableFrame("Save / Load Project", collapsed=False):
                    with ui.VStack(spacing=8):
                        project_file = string_input("Project File", "C:/temp/drone_project.json")

                        def run_save_project():
                            settings = collect_ui_settings(
                                area_min_x, area_max_x, area_min_y, area_max_y,
                                grid_spacing, grid_padding, grid_height, grid_size, grid_color, grid_debug,
                                path_spacing, path_padding, path_height, path_point_size, path_mode, visual_mode, path_color, path_debug,
                                ghost_speed_field, camera_mode_combo
                            )
                            save_project(project_file.model.get_value_as_string(), settings)

                        with ui.HStack(height=32):
                            ui.Button("Save Project", clicked_fn=run_save_project)
                            ui.Button("Load Project", clicked_fn=lambda: load_project(
                                project_file.model.get_value_as_string(),
                                combo_value(path_color, COLOR_OPTIONS),
                                path_point_size.model.get_value_as_float()
                            ))

                with ui.CollapsableFrame("Ghost Preview", collapsed=False):
                    with ui.VStack(spacing=8):
                        ghost_speed_field = float_input("Ghost Speed", 1.0)

                        ui.Label("Camera Mode")
                        camera_mode_combo = ui.ComboBox(0, *CAMERA_MODES)

                        def apply_ghost_settings():
                            global ghost_speed_value, ghost_camera_mode
                            ghost_speed_value = ghost_speed_field.model.get_value_as_float()
                            ghost_camera_mode = combo_value(camera_mode_combo, CAMERA_MODES)
                            print("Ghost Speed:", ghost_speed_value)
                            print("Camera Mode:", ghost_camera_mode)

                        ui.Button("Apply Ghost Settings", clicked_fn=apply_ghost_settings)

                        with ui.HStack(height=32):
                            ui.Button("Start Ghost Preview", clicked_fn=start_ghost_preview)
                            ui.Button("Stop Ghost Preview", clicked_fn=stop_ghost_preview)

                        ui.Button("Clear Ghost", clicked_fn=clear_ghost)

                with ui.CollapsableFrame("Clear Tools", collapsed=False):
                    with ui.VStack(spacing=8):
                        with ui.HStack(height=32):
                            ui.Button("Clear Path", clicked_fn=clear_path)
                            ui.Button("Clear Grid", clicked_fn=clear_nav_grid)

                        with ui.HStack(height=32):
                            ui.Button("Clear Markers", clicked_fn=clear_markers)
                            ui.Button("Clear Debug", clicked_fn=clear_debug)

                        with ui.HStack(height=32):
                            ui.Button("Clear Ghost", clicked_fn=clear_ghost)
                            ui.Button("Clear Ignore", clicked_fn=clear_ignore)

                ui.Spacer(height=12)
                ui.Label("Tip: Add GroundPlane and drone to Ignore List before generating.")

    refresh_labels()


create_gui()