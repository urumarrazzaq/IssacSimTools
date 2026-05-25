import omni.usd
import omni.ui as ui
from pxr import Usd, UsdGeom, Gf
import heapq
import math

stage = omni.usd.get_context().get_stage()
usd_context = omni.usd.get_context()
selection = usd_context.get_selection()

window = None
ignored_paths = set()
ignored_label = None


def float_input(label, default):
    with ui.HStack(height=28):
        ui.Label(label, width=150)
        field = ui.FloatField(width=120)
        field.model.set_value(default)
    return field


def get_world_bbox(prim):
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_],
        useExtentsHint=True
    )
    bbox = bbox_cache.ComputeWorldBound(prim)
    box = bbox.ComputeAlignedBox()
    return box.GetMin(), box.GetMax()


def update_ignore_text():
    if ignored_label:
        ignored_label.text = "Ignored Objects: None" if not ignored_paths else "Ignored Objects:\n" + "\n".join(ignored_paths)


def add_selected_to_ignore():
    selected_paths = selection.get_selected_prim_paths()
    if not selected_paths:
        print("No object selected.")
        return

    for path in selected_paths:
        ignored_paths.add(str(path))
        print("Added to ignore:", path)

    update_ignore_text()


def clear_ignore_list():
    ignored_paths.clear()
    update_ignore_text()
    print("Ignore list cleared.")


def is_ignored(prim):
    prim_path = str(prim.GetPath())
    return any(prim_path.startswith(ignored) for ignored in ignored_paths)


def clear_generated():
    if stage.GetPrimAtPath("/World/GeneratedDronePath"):
        stage.RemovePrim("/World/GeneratedDronePath")
    print("Cleared generated path.")


def collect_obstacles(obstacle_padding):
    obstacles = []

    for prim in stage.Traverse():
        path = str(prim.GetPath())

        if path.startswith("/World/GeneratedDronePath"):
            continue

        if is_ignored(prim):
            continue

        if prim.IsA(UsdGeom.Capsule) or prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Cube):
            try:
                min_pt, max_pt = get_world_bbox(prim)
                obstacles.append((
                    min_pt[0] - obstacle_padding,
                    max_pt[0] + obstacle_padding,
                    min_pt[1] - obstacle_padding,
                    max_pt[1] + obstacle_padding
                ))
            except Exception as e:
                print("Failed obstacle:", prim.GetPath(), e)

    print("Total obstacles:", len(obstacles))
    return obstacles


def is_blocked(x, y, obstacles):
    for min_x, max_x, min_y, max_y in obstacles:
        if min_x <= x <= max_x and min_y <= y <= max_y:
            return True
    return False


def create_path_point(root_path, index, x, y, z, size):
    cube = UsdGeom.Cube.Define(stage, f"{root_path}/PathPoint_{index}")
    cube.AddTranslateOp().Set(Gf.Vec3f(x, y, z))
    cube.AddScaleOp().Set(Gf.Vec3f(size, size, 0.04))
    cube.GetDisplayColorAttr().Set([Gf.Vec3f(1.0, 0.0, 0.0)])


def heuristic(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def astar(start, goal, min_x, max_x, min_y, max_y, step, obstacles):
    open_set = []
    heapq.heappush(open_set, (0, start))

    came_from = {}
    g_score = {start: 0}

    directions = [
        (1, 0), (-1, 0),
        (0, 1), (0, -1),
        (1, 1), (1, -1),
        (-1, 1), (-1, -1)
    ]

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        for dx, dy in directions:
            nx = current[0] + dx
            ny = current[1] + dy

            world_x = min_x + nx * step
            world_y = min_y + ny * step

            if world_x < min_x or world_x > max_x:
                continue
            if world_y < min_y or world_y > max_y:
                continue
            if is_blocked(world_x, world_y, obstacles):
                continue

            neighbor = (nx, ny)
            move_cost = 1.4 if dx != 0 and dy != 0 else 1.0
            tentative_g = g_score[current] + move_cost

            if neighbor not in g_score or tentative_g < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f = tentative_g + heuristic(neighbor, goal)
                heapq.heappush(open_set, (f, neighbor))

    return []


def world_to_grid(x, y, min_x, min_y, step):
    return (
        int(round((x - min_x) / step)),
        int(round((y - min_y) / step))
    )


def grid_to_world(cell, min_x, min_y, step):
    return (
        min_x + cell[0] * step,
        min_y + cell[1] * step
    )


def generate_astar_path(
    area_min_x,
    area_max_x,
    area_min_y,
    area_max_y,
    start_x,
    start_y,
    end_x,
    end_y,
    point_spacing,
    obstacle_padding,
    path_height,
    point_size
):
    clear_generated()

    obstacles = collect_obstacles(obstacle_padding)

    start = world_to_grid(start_x, start_y, area_min_x, area_min_y, point_spacing)
    goal = world_to_grid(end_x, end_y, area_min_x, area_min_y, point_spacing)

    path = astar(
        start,
        goal,
        area_min_x,
        area_max_x,
        area_min_y,
        area_max_y,
        point_spacing,
        obstacles
    )

    if not path:
        print("No valid path found. Try lower padding or move start/end.")
        return

    root_path = "/World/GeneratedDronePath"
    UsdGeom.Xform.Define(stage, root_path)

    for i, cell in enumerate(path):
        x, y = grid_to_world(cell, area_min_x, area_min_y, point_spacing)
        create_path_point(root_path, i, x, y, path_height, point_size)

    print("Generated connected A* path points:", len(path))


def create_gui():
    global window, ignored_label

    window = ui.Window("A* Drone Path Generator", width=430, height=720)

    with window.frame:
        with ui.VStack(spacing=8):

            ui.Label("Connected Drone Path Around Obstacles", height=30)

            area_min_x = float_input("Area Min X", -20)
            area_max_x = float_input("Area Max X", 20)
            area_min_y = float_input("Area Min Y", -20)
            area_max_y = float_input("Area Max Y", 20)

            start_x = float_input("Start X", -18)
            start_y = float_input("Start Y", -18)

            end_x = float_input("End X", 18)
            end_y = float_input("End Y", 18)

            point_spacing = float_input("Point Spacing", 1.0)
            obstacle_padding = float_input("Obstacle Padding", 2.0)
            path_height = float_input("Path Height", 0.25)
            point_size = float_input("Point Size", 0.25)

            ui.Spacer(height=10)

            ui.Button("Add Selected Object To Ignore", clicked_fn=add_selected_to_ignore)
            ui.Button("Clear Ignore List", clicked_fn=clear_ignore_list)

            ignored_label = ui.Label("Ignored Objects: None", height=100)

            ui.Spacer(height=10)

            def on_generate():
                generate_astar_path(
                    area_min_x.model.get_value_as_float(),
                    area_max_x.model.get_value_as_float(),
                    area_min_y.model.get_value_as_float(),
                    area_max_y.model.get_value_as_float(),
                    start_x.model.get_value_as_float(),
                    start_y.model.get_value_as_float(),
                    end_x.model.get_value_as_float(),
                    end_y.model.get_value_as_float(),
                    point_spacing.model.get_value_as_float(),
                    obstacle_padding.model.get_value_as_float(),
                    path_height.model.get_value_as_float(),
                    point_size.model.get_value_as_float()
                )

            ui.Button("Generate Connected Path", clicked_fn=on_generate)
            ui.Button("Clear Generated Path", clicked_fn=clear_generated)

            ui.Spacer(height=10)

            ui.Label("Use GroundPlane in ignore list.")
            ui.Label("Path root: /World/GeneratedDronePath")
            ui.Label("Path points: PathPoint_0, PathPoint_1...")


create_gui()
