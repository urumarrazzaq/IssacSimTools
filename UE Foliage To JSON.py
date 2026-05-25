import unreal
import json
import os

save_path = r"C:\Temp\foliage_export.json"
os.makedirs(os.path.dirname(save_path), exist_ok=True)

output = []

world = unreal.EditorLevelLibrary.get_editor_world()

foliage_actors = unreal.GameplayStatics.get_all_actors_of_class(
    world,
    unreal.InstancedFoliageActor
)

for foliage_actor in foliage_actors:

    components = foliage_actor.get_components_by_class(
        unreal.InstancedStaticMeshComponent
    )

    for comp in components:

        mesh = comp.get_editor_property("static_mesh")

        if not mesh:
            continue

        mesh_path = mesh.get_path_name()
        count = comp.get_instance_count()

        for i in range(count):

            transform = comp.get_instance_transform(i, True)  # True = world space

            loc = transform.translation
            rot = transform.rotation.rotator()
            scale = transform.scale3d

            output.append({
                "mesh": mesh_path,
                "location": {
                    "x": loc.x,
                    "y": loc.y,
                    "z": loc.z
                },
                "rotation": {
                    "pitch": rot.pitch,
                    "yaw": rot.yaw,
                    "roll": rot.roll
                },
                "scale": {
                    "x": scale.x,
                    "y": scale.y,
                    "z": scale.z
                }
            })

with open(save_path, "w") as f:
    json.dump(output, f, indent=4)

print("================================")
print(f"Exported {len(output)} foliage instances")
print(f"Saved to: {save_path}")
print("================================")