// Standalone DXGI capture of the visible RuneScape client. See docs/screen-capture.rst.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

#include <algorithm>
#include <charconv>
#include <cstdint>
#include <cstdio>
#include <cwchar>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string_view>
#include <vector>

namespace
{
using Microsoft::WRL::ComPtr;

constexpr unsigned default_frame_count = 120;
constexpr unsigned max_numeric_argument = 32768;
constexpr DWORD capture_timeout_ms = 2000;
constexpr UINT bytes_per_pixel = 4;

void check(HRESULT result, const char *operation)
{
    if (FAILED(result))
    {
        char message[128];
        std::snprintf(message, sizeof(message), "%s failed (0x%08lX)", operation,
                      static_cast<unsigned long>(result));
        throw std::runtime_error(message);
    }
}

unsigned number(std::string_view text)
{
    unsigned value = 0;
    const auto end = text.data() + text.size();
    const auto result = std::from_chars(text.data(), end, value);
    if (result.ec != std::errc{} || result.ptr != end || value > max_numeric_argument)
    {
        throw std::runtime_error("Expected an integer between 0 and 32768");
    }
    return value;
}

RECT client_bounds(HWND window)
{
    wchar_t title[128]{};
    GetWindowTextW(window, title, ARRAYSIZE(title));
    if (GetForegroundWindow() != window ||
        (std::wcscmp(title, L"RuneScape") != 0 &&
         std::wcscmp(title, L"RuneScape (Compatibility Mode)") != 0))
    {
        throw std::runtime_error("Keep RuneScape in the foreground during capture");
    }
    RECT bounds{};
    POINT origin{};
    if (!GetClientRect(window, &bounds) || !ClientToScreen(window, &origin) || IsRectEmpty(&bounds))
    {
        throw std::runtime_error("RuneScape client area is unavailable");
    }
    OffsetRect(&bounds, origin.x, origin.y);
    return bounds;
}

struct Display
{
    ComPtr<IDXGIAdapter1> adapter;
    ComPtr<IDXGIOutput1> output;
    RECT bounds;
};

Display find_display(HMONITOR monitor)
{
    ComPtr<IDXGIFactory1> factory;
    check(CreateDXGIFactory1(IID_PPV_ARGS(factory.GetAddressOf())), "CreateDXGIFactory1");
    for (UINT adapter_index = 0;; ++adapter_index)
    {
        ComPtr<IDXGIAdapter1> adapter;
        const auto adapter_result = factory->EnumAdapters1(adapter_index, adapter.GetAddressOf());
        if (adapter_result == DXGI_ERROR_NOT_FOUND)
        {
            throw std::runtime_error("Could not find the RuneScape display");
        }
        check(adapter_result, "EnumAdapters1");
        for (UINT output_index = 0;; ++output_index)
        {
            ComPtr<IDXGIOutput> output;
            const auto output_result = adapter->EnumOutputs(output_index, output.GetAddressOf());
            if (output_result == DXGI_ERROR_NOT_FOUND)
            {
                break;
            }
            check(output_result, "EnumOutputs");
            DXGI_OUTPUT_DESC description{};
            check(output->GetDesc(&description), "GetDesc");
            if (description.AttachedToDesktop && description.Monitor == monitor)
            {
                if (description.Rotation != DXGI_MODE_ROTATION_IDENTITY)
                {
                    throw std::runtime_error("Capture requires an unrotated display");
                }
                Display display{adapter, {}, description.DesktopCoordinates};
                check(output.As(&display.output), "Query IDXGIOutput1");
                return display;
            }
        }
    }
}

double now_ms()
{
    static const auto frequency = []
    {
        LARGE_INTEGER counter{};
        QueryPerformanceFrequency(&counter);
        return counter.QuadPart;
    }();
    LARGE_INTEGER counter{};
    QueryPerformanceCounter(&counter);
    return counter.QuadPart * 1000.0 / frequency;
}

void print_times(const char *label, std::vector<double> &times)
{
    std::sort(times.begin(), times.end());
    std::printf("%s p50 %.3f ms, p95 %.3f ms\n", label, times[times.size() / 2],
                times[(times.size() * 95 + 99) / 100 - 1]);
}

void write_bitmap(const std::filesystem::path &path, UINT width, UINT height,
                  const std::vector<std::uint8_t> &pixels)
{
    BITMAPFILEHEADER file_header{};
    BITMAPINFOHEADER bitmap_header{};
    file_header.bfType = 0x4D42; // BMP signature, 'BM' in little endian.
    file_header.bfOffBits = sizeof(file_header) + sizeof(bitmap_header);
    file_header.bfSize = file_header.bfOffBits + static_cast<DWORD>(pixels.size());
    bitmap_header.biSize = sizeof(bitmap_header);
    bitmap_header.biWidth = static_cast<LONG>(width);
    bitmap_header.biHeight = -static_cast<LONG>(height);
    bitmap_header.biPlanes = 1;
    bitmap_header.biBitCount = bytes_per_pixel * 8;
    std::ofstream file(path, std::ios::binary);
    file.write(reinterpret_cast<const char *>(&file_header), sizeof(file_header));
    file.write(reinterpret_cast<const char *>(&bitmap_header), sizeof(bitmap_header));
    file.write(reinterpret_cast<const char *>(pixels.data()), pixels.size());
    file.close();
    if (!file)
    {
        throw std::runtime_error("Could not write the BMP");
    }
}

void capture(unsigned frame_count, const RECT *crop, const std::filesystem::path &save_path)
{
    if (!SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    {
        throw std::runtime_error("Could not enable physical-pixel coordinates");
    }
    const HWND window = GetForegroundWindow();
    const RECT client = client_bounds(window);
    const double started = now_ms();
    const auto display = find_display(MonitorFromWindow(window, MONITOR_DEFAULTTONULL));
    RECT region = client;
    if (crop)
    {
        if (IsRectEmpty(crop) || crop->right > client.right - client.left ||
            crop->bottom > client.bottom - client.top)
        {
            throw std::runtime_error("Region must fit inside the RuneScape client");
        }
        region = *crop;
        OffsetRect(&region, client.left, client.top);
    }
    RECT visible{};
    if (!IntersectRect(&visible, &region, &display.bounds) || !EqualRect(&visible, &region))
    {
        throw std::runtime_error("Capture region must fit on the client's display");
    }
    OffsetRect(&region, -display.bounds.left, -display.bounds.top);
    const auto width = static_cast<UINT>(region.right - region.left);
    const auto height = static_cast<UINT>(region.bottom - region.top);
    const D3D11_BOX source_box{
        static_cast<UINT>(region.left),  static_cast<UINT>(region.top),    0,
        static_cast<UINT>(region.right), static_cast<UINT>(region.bottom), 1};

    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    check(D3D11CreateDevice(display.adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, nullptr, 0,
                            D3D11_SDK_VERSION, device.GetAddressOf(), nullptr,
                            context.GetAddressOf()),
          "D3D11CreateDevice");
    ComPtr<IDXGIOutputDuplication> duplication;
    check(display.output->DuplicateOutput(device.Get(), duplication.GetAddressOf()),
          "DuplicateOutput");
    D3D11_TEXTURE2D_DESC description{};
    description.Width = width;
    description.Height = height;
    description.MipLevels = 1;
    description.ArraySize = 1;
    description.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    description.SampleDesc.Count = 1;
    description.Usage = D3D11_USAGE_STAGING;
    description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ComPtr<ID3D11Texture2D> staging;
    check(device->CreateTexture2D(&description, nullptr, staging.GetAddressOf()),
          "CreateTexture2D");

    std::vector<std::uint8_t> saved_pixels;
    if (!save_path.empty())
    {
        saved_pixels.resize(static_cast<size_t>(width) * height * bytes_per_pixel);
    }
    std::vector<double> readback_times;
    readback_times.reserve(frame_count);
    double last_frame = started;
    double first_frame = 0;
    while (readback_times.size() < frame_count)
    {
        DXGI_OUTDUPL_FRAME_INFO frame_info{};
        ComPtr<IDXGIResource> resource;
        const auto result =
            duplication->AcquireNextFrame(capture_timeout_ms, &frame_info, resource.GetAddressOf());
        if (result == DXGI_ERROR_WAIT_TIMEOUT)
        {
            throw std::runtime_error("Timed out waiting for a desktop image update");
        }
        check(result, "AcquireNextFrame");
        if (frame_info.LastPresentTime.QuadPart == 0)
        {
            if (now_ms() - last_frame > capture_timeout_ms)
            {
                throw std::runtime_error("Timed out waiting for a desktop image update");
            }
        }
        else
        {
            const RECT current_client = client_bounds(window);
            if (!EqualRect(&current_client, &client))
            {
                throw std::runtime_error("RuneScape moved or resized during capture");
            }
            ComPtr<ID3D11Texture2D> texture;
            check(resource.As(&texture), "Query ID3D11Texture2D");
            const double readback_started = now_ms();
            context->CopySubresourceRegion(staging.Get(), 0, 0, 0, 0, texture.Get(), 0,
                                           &source_box);
            D3D11_MAPPED_SUBRESOURCE mapped{};
            check(context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped),
                  "Map staging texture");
            // Consume CPU pixels here while the buffer is mapped. Only the saved image needs a
            // copy.
            last_frame = now_ms();
            readback_times.push_back(last_frame - readback_started);
            if (readback_times.size() == 1)
            {
                first_frame = last_frame;
            }
            if (!saved_pixels.empty() && readback_times.size() == frame_count)
            {
                const auto pixels = static_cast<const std::uint8_t *>(mapped.pData);
                const size_t row_bytes = static_cast<size_t>(width) * bytes_per_pixel;
                for (UINT row = 0; row < height; ++row)
                {
                    std::copy_n(pixels + static_cast<size_t>(row) * mapped.RowPitch, row_bytes,
                                saved_pixels.data() + row * row_bytes);
                }
            }
            context->Unmap(staging.Get(), 0);
        }
        resource.Reset();
        check(duplication->ReleaseFrame(), "ReleaseFrame");
    }
    duplication.Reset();
    std::printf("%ux%u, %u desktop image updates, first frame %.1f ms\n", width, height,
                frame_count, first_frame - started);
    if (frame_count > 1)
    {
        std::printf("%.2f updates/s\n", 1000.0 * (frame_count - 1) / (last_frame - first_frame));
    }
    print_times("GPU crop to CPU map", readback_times);
    if (!saved_pixels.empty())
    {
        write_bitmap(save_path, width, height, saved_pixels);
        std::puts("Saved final frame as BMP");
    }
}
} // namespace

/// Capture the foreground client using DXGI Desktop Duplication.
int main(int argc, char **argv)
{
    try
    {
        unsigned frame_count = default_frame_count;
        RECT crop{};
        bool use_crop = false;
        std::filesystem::path save_path;
        for (int index = 1; index < argc; ++index)
        {
            const std::string_view option(argv[index]);
            if (option == "--frames" && index + 1 < argc)
            {
                frame_count = number(argv[++index]);
                if (frame_count == 0)
                {
                    throw std::runtime_error("Frame count must be positive");
                }
            }
            else if (option == "--region" && index + 4 < argc)
            {
                const auto x = static_cast<LONG>(number(argv[++index]));
                const auto y = static_cast<LONG>(number(argv[++index]));
                const auto width = static_cast<LONG>(number(argv[++index]));
                const auto height = static_cast<LONG>(number(argv[++index]));
                crop = {x, y, x + width, y + height};
                use_crop = true;
            }
            else if (option == "--save" && index + 1 < argc)
            {
                save_path = argv[++index];
            }
            else
            {
                std::puts("Usage: capture_screen [--frames N] [--region X Y WIDTH HEIGHT] "
                          "[--save FRAME.bmp]");
                return option == "--help" ? 0 : 2;
            }
        }
        capture(frame_count, use_crop ? &crop : nullptr, save_path);
    }
    catch (const std::exception &error)
    {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
    return 0;
}
