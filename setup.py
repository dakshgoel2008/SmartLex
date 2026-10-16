import sys
from setuptools import Extension, setup

extra_compile_args = ["-std=c++20", "-O3", "-Wall", "-Wextra"]
if sys.platform == "win32":
    extra_compile_args = ["/std:c++20", "/O2", "/EHsc"]

smartlex_core_ext = Extension(
    name="smartlex.core._smartlex_core",
    sources=[
        "cpp/src/tokenizer.cpp",
        "cpp/src/inverted_index.cpp",
        "cpp/src/scanner.cpp",
        "cpp/bindings/py_smartlex_core.cpp",
    ],
    include_dirs=["cpp/include"],
    language="c++",
    extra_compile_args=extra_compile_args,
)

if __name__ == "__main__":
    setup(ext_modules=[smartlex_core_ext])
