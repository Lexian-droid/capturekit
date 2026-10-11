# Verified vtable indices and IIDs

All indices used in `src/capturekit/_backends/windows/` and exercised by
tests on Windows 11 26200 (2026-10-11). IUnknown = 0..2, IInspectable = 3..5.

## DXGI / D3D11 (IUnknown → IDXGIObject 3..6)
| Interface | IID | Methods used (index) |
| --- | --- | --- |
| IDXGIFactory1 | 770aae78-f26f-4dba-a829-253c83d1b387 | EnumAdapters1 12 |
| IDXGIAdapter1 | – | EnumOutputs 7, GetDesc1 10 |
| IDXGIOutput | – | GetDesc 7 |
| IDXGIOutput1 | **00cddea8-939b-4b83-a340-a685226666cc** (verified vs MinGW dxgi1_2.idl; an earlier guess was wrong → E_NOINTERFACE) | DuplicateOutput 22 |
| IDXGIOutputDuplication | 191cfac3-a341-470d-b26e-a864f428319c | GetDesc 7, AcquireNextFrame 8, GetFrameDirtyRects 9, GetFrameMoveRects 10, GetFramePointerShape 11, MapDesktopSurface 12, UnMapDesktopSurface 13, ReleaseFrame 14 |
| IDXGIDevice | 54ec77fa-1377-44e6-8c32-88fd5f44c84c | (QI only) |
| ID3D11Device | – | CreateTexture2D 5, GetImmediateContext 40 |
| ID3D11DeviceContext | – | Map 14, Unmap 15, CopySubresourceRegion 46, CopyResource 47, Flush 111 |
| ID3D11Texture2D | 6f15aaf2-d208-4e89-9ab4-489535d34f9c | GetDesc 10 |
| ID3D10Multithread | 9B7E4E00-342C-4106-A19F-4F2704F689F0 | SetMultithreadProtected 5 |

## Windows Graphics Capture (WinRT)
| Interface | IID | Methods |
| --- | --- | --- |
| IGraphicsCaptureItemInterop (IUnknown) | 3628E81B-3CAC-4C60-B7F4-23CE0E0C3356 | CreateForWindow 3, CreateForMonitor 4 |
| IGraphicsCaptureItem | 79C3F95B-31F7-4EC2-A464-632EF5D30760 | get_DisplayName 6, get_Size 7, add_Closed 8, remove_Closed 9 |
| IDirect3D11CaptureFramePoolStatics2 | 589B103F-6BBC-5DF5-A991-02E28B3B66D5 | CreateFreeThreaded 6 |
| IDirect3D11CaptureFramePool | 24EB6D22-1975-422E-82E7-780DBD8DDF24 | Recreate 6, TryGetNextFrame 7, add_FrameArrived 8, remove_FrameArrived 9, CreateCaptureSession 10 |
| IGraphicsCaptureSession | 814E42A9-F70F-4AD7-939B-FDDCC6EB880D | StartCapture 6 |
| IGraphicsCaptureSession2 | 2C39AE40-7D2E-5044-804E-8B6799D4CF9E | put_IsCursorCaptureEnabled 7 |
| IGraphicsCaptureSession3 | F2CDD966-22AE-5EA1-9596-3A289344C3BE | put_IsBorderRequired 7 |
| IGraphicsCaptureSessionStatics | 2224A540-5974-49AA-B232-0882536F4CB5 | IsSupported 6 |
| IDirect3D11CaptureFrame | FA50C623-38DA-4B32-ACF3-FA9734AD800E | get_Surface 6, get_SystemRelativeTime 7, get_ContentSize 8 |
| IDirect3DDxgiInterfaceAccess (IUnknown) | A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1 | GetInterface 3 |
| IClosable | 30D5A829-7FA4-4026-83BB-D75BAE4EA99E | Close 6 |
| TypedEventHandler<FramePool, Object> | 51a947f7-79cf-5a3e-a3a5-1289cfa6dfe8 (computed by `pinterface_iid`, matches published value) | Invoke 3 |

`CreateDirect3D11DeviceFromDXGIDevice` (d3d11.dll export) wraps the device.
`SizeInt32` is passed by value.

## WASAPI / MMDevice
| Interface | IID | Methods |
| --- | --- | --- |
| IMMDeviceEnumerator (CLSID BCDE0395-E52F-467C-8E3D-C4579291692E) | A95664D2-9614-4F35-A746-DE8DB63617E6 | EnumAudioEndpoints 3, GetDefaultAudioEndpoint 4, GetDevice 5 |
| IMMDeviceCollection | – | GetCount 3, Item 4 |
| IMMDevice | – | Activate 3, OpenPropertyStore 4, GetId 5 |
| IPropertyStore | – | GetValue 5 |
| IAudioClient | 1CB9AD4C-DBFA-4c32-B178-C2F568A703B2 | Initialize 3, Start 10, Stop 11, SetEventHandle 13, GetService 14 |
| IAudioCaptureClient | C8ADBD64-E71E-48a0-A4DE-185C395CD317 | GetBuffer 3, ReleaseBuffer 4, GetNextPacketSize 5 |
| IAudioSessionManager2 | 77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F | GetSessionEnumerator 5 |
| IAudioSessionEnumerator | – | GetCount 3, GetSession 4 |
| IAudioSessionControl2 | bfb7ff88-7239-4fc9-8fa2-07c950be9c6d | GetState 3, GetDisplayName 4, GetProcessId 14, IsSystemSoundsSession 15 |
| IActivateAudioInterfaceCompletionHandler | 41D949AB-9862-444A-80F6-C261334DA5EB | ActivateCompleted 3 |
| IActivateAudioInterfaceAsyncOperation | – | GetActivateResult 3 |
