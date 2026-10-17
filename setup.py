import os
import sys
import sysconfig

from setuptools import Extension, find_packages, setup

extra_compile_args = ["-std=c++20", "-O3", "-Wall", "-Wextra"]
if sys.platform == "win32":
    extra_compile_args = ["/std:c++20", "/O2", "/EHsc"]


def _version_tuple(version):
    return tuple(int(part) for part in version.split(".") if part.isdigit())


# std::filesystem needs macOS 10.15+, but some Python builds (e.g. python.org 3.10) target 10.9.
if sys.platform == "darwin":
    target = os.environ.get("MACOSX_DEPLOYMENT_TARGET") or sysconfig.get_config_var("MACOSX_DEPLOYMENT_TARGET") or ""
    if not target or _version_tuple(target) < (10, 15):
        os.environ["MACOSX_DEPLOYMENT_TARGET"] = "10.15"

smartlex_core_ext = Extension(
    name="smartlex.core._smartlex_core",
    sources=[
        "cpp/src/tokenizer.cpp",
        "cpp/src/inverted_index.cpp",
        "cpp/src/scanner.cpp",
        "cpp/bindings/py_smartlex_core.cpp",
    ],
    depends=[
        "cpp/include/smartlex/varint.hpp",
        "cpp/include/smartlex/tokenizer.hpp",
        "cpp/include/smartlex/inverted_index.hpp",
        "cpp/include/smartlex/scanner.hpp",
    ],
    include_dirs=["cpp/include"],
    language="c++",
    extra_compile_args=extra_compile_args,
)

if __name__ == "__main__":
    setup(
        package_dir={"": "src"},
        packages=find_packages(where="src"),
        ext_modules=[smartlex_core_ext],
    )
