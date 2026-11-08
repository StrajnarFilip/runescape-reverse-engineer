cmake_minimum_required(VERSION 3.20)
project(rs3_native VERSION 1.0.0 LANGUAGES C)

if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "rs3_native requires a Windows x64 toolchain")
endif()

include(CTest)
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)
find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(profile "${CMAKE_CURRENT_SOURCE_DIR}/profiles/profile_951_3.json")
set(position_profile "${CMAKE_CURRENT_SOURCE_DIR}/profiles/profile_6ada3f59.json")
set(generated_dir "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(profile_header "${generated_dir}/profile_generated.h")
add_custom_command(
    OUTPUT "${profile_header}"
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/generate_profile.py"
            "${profile}" "${position_profile}" "${profile_header}"
    DEPENDS tools/generate_profile.py "${profile}" "${position_profile}"
    VERBATIM
)

# The DLL and the decoder fixtures use the same compiled implementation.
add_library(rs3_objects OBJECT
    src/rs3_native.c
    src/process.c
    src/decode.c
    src/position.c
    src/npcs.c
    src/inventory.c
    "${profile_header}"
)
set_target_properties(rs3_objects PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)
target_include_directories(rs3_objects PRIVATE include src "${generated_dir}")
target_compile_definitions(rs3_objects PRIVATE RS3_BUILD WIN32_LEAN_AND_MEAN _WIN32_WINNT=0x0A00)
if(MSVC)
    target_compile_definitions(rs3_objects PRIVATE _CRT_SECURE_NO_WARNINGS)
    target_compile_options(rs3_objects PRIVATE /W4)
else()
    target_compile_options(rs3_objects PRIVATE -Wall -Wextra)
endif()

add_library(rs3_native SHARED)
add_library(rs3::native ALIAS rs3_native)
set_target_properties(rs3_native PROPERTIES EXPORT_NAME native PREFIX "")
target_link_libraries(rs3_native PRIVATE rs3_objects bcrypt user32)
target_include_directories(rs3_native PUBLIC
    "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
    "$<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>"
)

add_executable(read_example examples/read.c)
target_include_directories(read_example PRIVATE include)
set_target_properties(read_example PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)
add_dependencies(read_example rs3_native)

if(BUILD_TESTING)
    add_executable(test_native tests/native/test_native.c)
    set_target_properties(test_native PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)
    target_include_directories(test_native PRIVATE include src "${generated_dir}")
    target_compile_definitions(test_native PRIVATE RS3_STATIC WIN32_LEAN_AND_MEAN _WIN32_WINNT=0x0A00)
    if(MSVC)
        target_compile_definitions(test_native PRIVATE _CRT_SECURE_NO_WARNINGS)
    endif()
    target_link_libraries(test_native PRIVATE rs3_objects bcrypt user32)
    add_test(NAME native COMMAND test_native)
    add_test(NAME python COMMAND "${CMAKE_COMMAND}" -E env
        "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/python/rs3_reader;${CMAKE_CURRENT_SOURCE_DIR}/work/reference"
        "RS3_NATIVE_LIBRARY=$<TARGET_FILE:rs3_native>"
        "${Python3_EXECUTABLE}" -m unittest discover -s "${CMAKE_CURRENT_SOURCE_DIR}/tests/python" -v
    )
endif()

set(RS3_CACHE_ROOT "C:/ProgramData/Jagex/RuneScape" CACHE PATH "Installed read-only game cache")
add_custom_target(rs3_catalog
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/generate_catalog.py"
            --cache-root "${RS3_CACHE_ROOT}"
            --output "$<TARGET_FILE_DIR:rs3_native>/rs3_catalog.bin"
    DEPENDS rs3_native
    VERBATIM
)

set(package_dir "${CMAKE_INSTALL_LIBDIR}/cmake/rs3_native")
install(TARGETS rs3_native EXPORT rs3_native_targets
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
)
install(FILES include/rs3/rs3.h DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/rs3")
install(EXPORT rs3_native_targets NAMESPACE rs3:: DESTINATION "${package_dir}")
configure_package_config_file(cmake/rs3_native-config.cmake.in
    "${CMAKE_CURRENT_BINARY_DIR}/rs3_native-config.cmake" INSTALL_DESTINATION "${package_dir}")
write_basic_package_version_file("${CMAKE_CURRENT_BINARY_DIR}/rs3_native-config-version.cmake"
    COMPATIBILITY SameMajorVersion)
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/rs3_native-config.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/rs3_native-config-version.cmake"
    DESTINATION "${package_dir}"
)
