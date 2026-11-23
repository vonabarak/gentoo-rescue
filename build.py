import os
from pathlib import Path

from corvus_image_builder.core import BuildContext, BuildManager, ImageRecipe
from corvus_image_builder.gentoo import (
    ChrootEnvironmentStage,
    KernelStage,
    Settings,
    WorkspaceStage,
)
from rescue_stages import (
    ASSETS,
    RescueBootloaderStage,
    RescueCleanupStage,
    RescueDiskStage,
    RescueKernelStage,
    RescuePackagesStage,
    RescuePackStage,
    RescueRepositoryStage,
    RescueRuntimeStage,
)


def build_recipe(settings: Settings) -> ImageRecipe:
    """Compose the RAM-staged removable rescue disk build."""
    return ImageRecipe(
        [
            WorkspaceStage(settings),
            RescueRepositoryStage(settings),
            RescueDiskStage(settings),
            ChrootEnvironmentStage(settings),
            RescuePackagesStage(settings),
            RescueRuntimeStage(settings),
            KernelStage(settings, cache_prefix="gentoo-rescue"),
            RescueKernelStage(settings),
            RescueCleanupStage(settings),
            RescuePackStage(settings),
            RescueBootloaderStage(settings),
        ]
    )


if __name__ == "__main__":
    settings = Settings(
        name="gentoo-rescue",
        disk=Path(os.environ.get("TARGET_DISK", "/dev/vdb")),
        cache=Path(os.environ.get("IMAGE_CACHE", "/mnt/corvus-cache")),
        kernel_config=ASSETS / "kernel/rescue-amd64.config",
        use_binary=os.environ.get("USEPKG", "true") == "true",
        fetch_binary=os.environ.get("GETBINPKG", "true") == "true",
    )
    context = BuildContext(Path(os.environ.get("IMAGE_ROOT", "/mnt/sysroot")).resolve())
    manager = BuildManager()
    recipe = build_recipe(settings)
    if os.environ.get("IMAGE_BUILD_PLAN") == "1":
        print("\n".join(manager.plan(recipe, context)))
    else:
        manager.run(recipe, context)
