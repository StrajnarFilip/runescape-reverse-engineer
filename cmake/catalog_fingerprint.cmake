# Records the size and modification time of each game cache archive in OUTPUT.
#
# OUTPUT is rewritten only when the fingerprint changes, so the catalog rule that
# depends on it reruns exactly when the installed cache changes. MSBuild cannot
# track the archives directly because it ignores files under ProgramData.
#
# Usage: cmake -DOUTPUT=<file> "-DARCHIVES=<first>|<second>" -P catalog_fingerprint.cmake

string(REPLACE "|" ";" archive_paths "${ARCHIVES}")
set(fingerprint "")
foreach(archive IN LISTS archive_paths)
    if(EXISTS "${archive}")
        file(TIMESTAMP "${archive}" modified "%Y-%m-%dT%H:%M:%SZ" UTC)
        file(SIZE "${archive}" size_bytes)
        string(APPEND fingerprint "${archive} ${size_bytes} ${modified}\n")
    else()
        string(APPEND fingerprint "${archive} missing\n")
    endif()
endforeach()

if(EXISTS "${OUTPUT}")
    file(READ "${OUTPUT}" previous_fingerprint)
    if(previous_fingerprint STREQUAL fingerprint)
        return()
    endif()
endif()
file(WRITE "${OUTPUT}" "${fingerprint}")
