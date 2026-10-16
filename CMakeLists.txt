cmake_minimum_required(VERSION 3.18)
project(SmartLexCore VERSION 2.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
find_package(Threads REQUIRED)

# Static C++20 core library (usable by both Python bindings and C++ tests/CLI)
add_library(smartlex_cpp_lib STATIC
    cpp/src/tokenizer.cpp
    cpp/src/inverted_index.cpp
    cpp/src/scanner.cpp
)

target_include_directories(smartlex_cpp_lib PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/cpp/include
)

target_link_libraries(smartlex_cpp_lib PUBLIC Threads::Threads)

if(MSVC)
    target_compile_options(smartlex_cpp_lib PRIVATE /O2 /W4)
else()
    target_compile_options(smartlex_cpp_lib PRIVATE -O3 -Wall -Wextra)
endif()

# Python C-Extension module (_smartlex_core)
Python3_add_library(_smartlex_core MODULE WITH_SOABI
    cpp/bindings/py_smartlex_core.cpp
)

target_link_libraries(_smartlex_core PRIVATE smartlex_cpp_lib)

# Copy compiled extension into src/smartlex/core/ so Python imports it directly
add_custom_command(TARGET _smartlex_core POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:_smartlex_core>
            ${CMAKE_CURRENT_SOURCE_DIR}/src/smartlex/core/$<TARGET_FILE_NAME:_smartlex_core>
    COMMENT "Copying _smartlex_core extension to src/smartlex/core/"
)
