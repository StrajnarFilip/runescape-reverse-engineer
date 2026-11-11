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

# ---------------------------------------------------------------------------
# Profile sources generated from the version-pinned JSON profiles
# ---------------------------------------------------------------------------
set(RS3_PRIMARY_PROFILE "${CMAKE_CURRENT_SOURCE_DIR}/profiles/profile_951_3.json")
set(RS3_COMPATIBILITY_PROFILE "${CMAKE_CURRENT_SOURCE_DIR}/profiles/profile_6ada3f59.json")
set(RS3_GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(RS3_PROFILE_OUTPUTS
    "${RS3_GENERATED_DIR}/profile_generated.h"
    "${RS3_GENERATED_DIR}/profile_generated.c"
)
add_custom_command(
    OUTPUT ${RS3_PROFILE_OUTPUTS}
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/generate_profile.py"
            "${RS3_PRIMARY_PROFILE}" "${RS3_COMPATIBILITY_PROFILE}" "${RS3_GENERATED_DIR}"
    DEPENDS tools/generate_profile.py "${RS3_PRIMARY_PROFILE}" "${RS3_COMPATIBILITY_PROFILE}"
    COMMENT "Generating client profile sources"
    VERBATIM
)

# Shared compile settings for every C target in this project.
function(rs3_configure_c_target target)
    set_target_properties(${target} PROPERTIES
        C_STANDARD 11
        C_STANDARD_REQUIRED ON
        C_EXTENSIONS OFF
    )
    target_compile_definitions(${target} PRIVATE WIN32_LEAN_AND_MEAN _WIN32_WINNT=0x0A00)
    if(MSVC)
        target_compile_definitions(${target} PRIVATE _CRT_SECURE_NO_WARNINGS)
        target_compile_options(${target} PRIVATE /W4)
    else()
        target_compile_options(${target} PRIVATE -Wall -Wextra)
    endif()
endfunction()

# ---------------------------------------------------------------------------
# Library: the DLL and the native tests share one compiled implementation
# ---------------------------------------------------------------------------
add_library(rs3_objects OBJECT
    src/abilities.c
    src/catalog.c
    src/clock.c
    src/combat.c
    src/context.c
    src/decode_utils.c
    src/event_queues.c
    src/event_ring.c
    src/frame.c
    src/input.c
    src/inventory.c
    src/memory.c
    src/npc_animation.c
    src/npcs.c
    src/player.c
    src/player_hits.c
    src/position.c
    src/prayer.c
    src/process.c
    src/snapshot.c
    src/variables.c
    ${RS3_PROFILE_OUTPUTS}
)
rs3_configure_c_target(rs3_objects)
target_include_directories(rs3_objects PRIVATE include src "${RS3_GENERATED_DIR}")
target_compile_definitions(rs3_objects PRIVATE RS3_BUILD)

add_library(rs3_native SHARED)
add_library(rs3::native ALIAS rs3_native)
set_target_properties(rs3_native PROPERTIES EXPORT_NAME native PREFIX "")
target_link_libraries(rs3_native PRIVATE rs3_objects bcrypt user32)
target_include_directories(rs3_native PUBLIC
    "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
    "$<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>"
)

# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------
add_executable(read_example examples/read.c)
rs3_configure_c_target(read_example)
target_include_directories(read_example PRIVATE include)
add_dependencies(read_example rs3_native)

add_executable(prayer_example examples/prayer.c)
rs3_configure_c_target(prayer_example)
target_link_libraries(prayer_example PRIVATE rs3::native)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
if(BUILD_TESTING)
    # Native fixture tests link the private objects statically and use fake memory.
    function(rs3_add_native_test name)
        add_executable(${name} ${ARGN})
        rs3_configure_c_target(${name})
        target_include_directories(${name} PRIVATE include src "${RS3_GENERATED_DIR}" tests/native)
        target_compile_definitions(${name} PRIVATE RS3_STATIC)
        target_link_libraries(${name} PRIVATE rs3_objects bcrypt user32)
        add_test(NAME ${name} COMMAND ${name})
    endfunction()

    set(RS3_FIXTURE_SOURCES tests/native/fixture.c)
    rs3_add_native_test(native_player tests/native/test_player.c ${RS3_FIXTURE_SOURCES})
    rs3_add_native_test(native_npcs tests/native/test_npcs.c ${RS3_FIXTURE_SOURCES})
    rs3_add_native_test(native_lifecycle tests/native/test_lifecycle.c ${RS3_FIXTURE_SOURCES})
    rs3_add_native_test(native_inventory tests/native/test_inventory.c ${RS3_FIXTURE_SOURCES})
    rs3_add_native_test(native_combat tests/native/test_combat.c ${RS3_FIXTURE_SOURCES})
    rs3_add_native_test(native_player_hits tests/native/test_player_hits.c ${RS3_FIXTURE_SOURCES})
    rs3_add_native_test(native_input tests/native/test_input.c ${RS3_FIXTURE_SOURCES})
    rs3_add_native_test(native_prayer_planner tests/native/test_prayer_planner.c)

    add_test(NAME prayer_example COMMAND prayer_example)

    add_test(NAME python COMMAND "${CMAKE_COMMAND}" -E env
        "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/python;${CMAKE_CURRENT_SOURCE_DIR}/work/reference"
        "RS3_NATIVE_LIBRARY=$<TARGET_FILE:rs3_native>"
        "${Python3_EXECUTABLE}" -m unittest discover -s "${CMAKE_CURRENT_SOURCE_DIR}/tests/python" -v
    )
endif()

# ---------------------------------------------------------------------------
# Metadata catalog generated from the installed, read-only game cache
# ---------------------------------------------------------------------------
# The DLL loads rs3_catalog.bin from its own directory; without it item names
# and ability metadata are unavailable. When the item (19) and struct (22)
# archives exist, the default build generates the catalog and regenerates it
# whenever an archive, a profile or the generator changes.
set(RS3_CACHE_ROOT "C:/ProgramData/Jagex/RuneScape" CACHE PATH "Installed read-only game cache")
set(RS3_CATALOG_ARCHIVES "${RS3_CACHE_ROOT}/js5-19.jcache" "${RS3_CACHE_ROOT}/js5-22.jcache")
set(RS3_CATALOG_CACHE_FOUND ON)
foreach(archive IN LISTS RS3_CATALOG_ARCHIVES)
    if(NOT EXISTS "${archive}")
        set(RS3_CATALOG_CACHE_FOUND OFF)
    endif()
endforeach()
option(RS3_BUILD_CATALOG "Generate rs3_catalog.bin when the game cache is installed" ON)

if(RS3_BUILD_CATALOG AND RS3_CATALOG_CACHE_FOUND)
    # OUTPUT cannot name a target directory, so mirror CMAKE_RUNTIME_OUTPUT_DIRECTORY.
    get_property(RS3_MULTI_CONFIG GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
    if(RS3_MULTI_CONFIG)
        set(RS3_CATALOG_OUTPUT "${CMAKE_RUNTIME_OUTPUT_DIRECTORY}/$<CONFIG>/rs3_catalog.bin")
    else()
        set(RS3_CATALOG_OUTPUT "${CMAKE_RUNTIME_OUTPUT_DIRECTORY}/rs3_catalog.bin")
    endif()
    # A cheap step on every build rewrites the fingerprint only when an archive changed.
    set(RS3_CATALOG_FINGERPRINT "${RS3_GENERATED_DIR}/catalog_archives.txt")
    string(REPLACE ";" "|" RS3_CATALOG_ARCHIVE_ARGUMENT "${RS3_CATALOG_ARCHIVES}")
    add_custom_target(rs3_catalog_fingerprint
        COMMAND "${CMAKE_COMMAND}" "-DOUTPUT=${RS3_CATALOG_FINGERPRINT}"
                "-DARCHIVES=${RS3_CATALOG_ARCHIVE_ARGUMENT}"
                -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/catalog_fingerprint.cmake"
        BYPRODUCTS "${RS3_CATALOG_FINGERPRINT}"
        COMMENT "Checking the installed game cache"
        VERBATIM
    )
    add_custom_command(
        OUTPUT "${RS3_CATALOG_OUTPUT}"
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/generate_catalog.py"
                --cache-root "${RS3_CACHE_ROOT}" --output "${RS3_CATALOG_OUTPUT}"
        DEPENDS
            tools/generate_catalog.py
            python/rs3_reader/cache_definitions.py
            "${RS3_PRIMARY_PROFILE}"
            profiles/ability_bindings_951_3.json
            "${RS3_CATALOG_FINGERPRINT}"
        COMMENT "Generating rs3_catalog.bin from the installed game cache"
        VERBATIM
    )
    # As a source of the DLL target the catalog is regenerated only when out of date;
    # a separate utility target would rerun the generator on every build.
    target_sources(rs3_native PRIVATE "${RS3_CATALOG_OUTPUT}")
    add_dependencies(rs3_native rs3_catalog_fingerprint)
    add_custom_target(rs3_catalog)
    add_dependencies(rs3_catalog rs3_native)
else()
    set(RS3_CATALOG_MISSING_MESSAGE
        "rs3_catalog.bin was not generated: set RS3_CACHE_ROOT to the installed game cache "
        "(js5-19.jcache and js5-22.jcache) and RS3_BUILD_CATALOG=ON, then reconfigure. "
        "Without it item names and ability metadata are unavailable.")
    message(STATUS "${RS3_CATALOG_MISSING_MESSAGE}")
    add_custom_target(rs3_catalog
        COMMAND "${CMAKE_COMMAND}" -E echo "${RS3_CATALOG_MISSING_MESSAGE}"
        COMMAND "${CMAKE_COMMAND}" -E false
        VERBATIM
    )
endif()

# ---------------------------------------------------------------------------
# Installation and CMake package
# ---------------------------------------------------------------------------
set(RS3_PACKAGE_DIR "${CMAKE_INSTALL_LIBDIR}/cmake/rs3_native")
install(TARGETS rs3_native EXPORT rs3_native_targets
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
)
install(FILES include/rs3/rs3.h include/rs3/combat.h include/rs3/prayer.h
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/rs3"
)
install(EXPORT rs3_native_targets NAMESPACE rs3:: DESTINATION "${RS3_PACKAGE_DIR}")
configure_package_config_file(cmake/rs3_native-config.cmake.in
    "${CMAKE_CURRENT_BINARY_DIR}/rs3_native-config.cmake"
    INSTALL_DESTINATION "${RS3_PACKAGE_DIR}"
)
write_basic_package_version_file("${CMAKE_CURRENT_BINARY_DIR}/rs3_native-config-version.cmake"
    COMPATIBILITY SameMajorVersion
)
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/rs3_native-config.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/rs3_native-config-version.cmake"
    DESTINATION "${RS3_PACKAGE_DIR}"
)
