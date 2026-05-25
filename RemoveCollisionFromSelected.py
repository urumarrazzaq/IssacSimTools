import omni.usd
from pxr import UsdPhysics, PhysxSchema

# Get current stage
stage = omni.usd.get_context().get_stage()

# Get selected objects
selection = omni.usd.get_context().get_selection().get_selected_prim_paths()

for prim_path in selection:
    prim = stage.GetPrimAtPath(prim_path)

    if prim.IsValid():

        # Remove Collision API
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            prim.RemoveAPI(UsdPhysics.CollisionAPI)

        # Remove PhysX Collision API
        if prim.HasAPI(PhysxSchema.PhysxCollisionAPI):
            prim.RemoveAPI(PhysxSchema.PhysxCollisionAPI)

        print(f"Collision removed from: {prim_path}")

print("Done!")
