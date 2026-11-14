RuneScape screen capture
========================

DXGI Desktop Duplication is the fastest tested path for sustained capture of
the visible RuneScape client. ``tools/capture_screen.cpp`` keeps one duplication
session open, waits for desktop image updates, and copies only the requested
rectangle into a reusable D3D11 staging texture. CPU consumers can read the
mapped BGRA rows using ``RowPitch`` while the texture is mapped.

Build from the repository root in an x64 Native Tools Command Prompt for
Visual Studio with the Windows SDK installed.

.. code-block:: bat

    if not exist build mkdir build
    cl /nologo /std:c++17 /EHsc /W4 /O2 tools\capture_screen.cpp /Fo:build\capture_screen.obj /Fe:build\capture_screen.exe /link d3d11.lib dxgi.lib user32.lib

Run from the repository root and switch to RuneScape during the delay.
``--region`` takes a client-relative physical pixel rectangle in the order
``X Y WIDTH HEIGHT``. Omit it to read the entire client. ``--save`` writes the
last captured frame as an uncompressed BMP. Use a path under ``build/`` to
keep images local.

.. code-block:: powershell

    Start-Sleep -Seconds 3
    .\build\capture_screen.exe --frames 300

.. code-block:: powershell

    Start-Sleep -Seconds 3
    .\build\capture_screen.exe --frames 300 --region 639 847 640 360 --save build\rs3.bmp

The tool captures visible desktop pixels and requires the game in the foreground.
Keep the region inside one unrotated display and clear of overlays. A window
move, resize, or loss of foreground ends the run. The update counter follows
Windows desktop image presents, which can include changes outside the crop.

Live validation on 2026-11-14
-----------------------------

RuneScape Compatibility Mode on Windows 10 22H2, Radeon RX 7600, and a 3840×2160
display at 60 Hz. Both DXGI runs collected 300 desktop image updates. Readback
measures the GPU region copy through the CPU map. File writing is outside the
measurement.

=================== ============ ============ ============ ============
Region              Updates/s    Median       95th pct.    First frame
=================== ============ ============ ============ ============
1918×2054 client    60.06        1.66 ms      3.97 ms      67.4 ms
640×360 crop        60.04        0.53 ms      1.48 ms      50.2 ms
=================== ============ ============ ============ ============

The existing MSS backend took a median of 17.01 ms to return full-client
BGRA and 33.39 ms with RGB conversion, over 100 warm captures per method.
The corresponding 640×360 MSS medians were 16.66 ms and 16.68 ms. A new MSS
session plus its first BGRA grab took a median of 33.25 ms for the full client,
so MSS is quicker to start for an isolated capture. PNG encoding, file writing,
and process startup were outside those timings.

A separate native Windows Graphics Capture test targeted the RuneScape window
directly and also reached 60 updates/s. In a concurrent test, 296 matching
640×360 samples reached CPU memory with a median delivery difference of less
than 0.01 ms between the two APIs. The DXGI session got its first frame in
51.4 ms, and the window capture session in 96.3 ms. The comparison used
matching pixel samples and the same local clock for arrival times.

See Microsoft's `Desktop Duplication API documentation
<https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api>`_
and `Windows Graphics Capture window interop documentation
<https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow>`_.
