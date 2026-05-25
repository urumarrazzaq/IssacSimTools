import omni.usd
from pxr import UsdPhysics, PhysxSchema

# Get current stage
stage = omni.usd.get_context().get_stage()

# Get selected objects
selection = omni.usd.get_context().get_selection().get_selected_prim_paths()

for prim_path in selection:
    prim = stage.GetPrimAtPath(prim_path)

    if prim.IsValid():
        # Add Collision API
        UsdPhysics.CollisionAPI.Apply(prim)

        # Add Mesh Collision API
        PhysxSchema.PhysxCollisionAPI.Apply(prim)

        print(f"Collision added to: {prim_path}")

print("Done!")
