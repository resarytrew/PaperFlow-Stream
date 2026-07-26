import { useCallback, useEffect, useRef, useState } from "react";

export interface CameraState {
  devices: MediaDeviceInfo[];
  deviceId: string | null;
  stream: MediaStream | null;
  error: string | null;
  resolution: [number, number] | null;
}

/** Manage getUserMedia lifecycle + device switching. */
export function useCamera(preferredWidth = 1920, preferredHeight = 1080) {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resolution, setResolution] = useState<[number, number] | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setStream(null);
  }, []);

  const start = useCallback(
    async (id?: string | null) => {
      stop();
      setError(null);
      try {
        const constraints: MediaStreamConstraints = {
          audio: false,
          video: {
            deviceId: id ? { exact: id } : undefined,
            width: { ideal: preferredWidth },
            height: { ideal: preferredHeight },
          },
        };
        const media = await navigator.mediaDevices.getUserMedia(constraints);
        streamRef.current = media;
        setStream(media);
        const track = media.getVideoTracks()[0];
        const settings = track.getSettings();
        setResolution([settings.width ?? 0, settings.height ?? 0]);
        if (settings.deviceId) setDeviceId(settings.deviceId);
        // Device labels are only available after permission is granted.
        const all = await navigator.mediaDevices.enumerateDevices();
        setDevices(all.filter((d) => d.kind === "videoinput"));
      } catch (e) {
        setError(
          e instanceof DOMException && e.name === "NotAllowedError"
            ? "Доступ к камере запрещён. Разрешите доступ в настройках браузера."
            : `Не удалось открыть камеру: ${(e as Error).message}`,
        );
      }
    },
    [preferredWidth, preferredHeight, stop],
  );

  useEffect(() => {
    start(null);
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { devices, deviceId, stream, error, resolution, start, stop, setDeviceId } as const;
}

let captureCanvas: HTMLCanvasElement | undefined;

function drawFrame(video: HTMLVideoElement, maxWidth: number): HTMLCanvasElement | null {
  if (!video.videoWidth || !video.videoHeight) return null;
  const scale = Math.min(1, maxWidth / video.videoWidth);
  const w = Math.round(video.videoWidth * scale);
  const h = Math.round(video.videoHeight * scale);
  const canvas = captureCanvas ?? (captureCanvas = document.createElement("canvas"));
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.drawImage(video, 0, 0, w, h);
  return canvas;
}

/** Grab the current video frame as a JPEG data URL (legacy JSON transport). */
export function captureFrame(video: HTMLVideoElement, maxWidth: number, quality: number): string | null {
  const canvas = drawFrame(video, maxWidth);
  return canvas ? canvas.toDataURL("image/jpeg", quality) : null;
}

/** Grab the current video frame as a JPEG Blob for binary WebSocket transport. */
export function captureFrameBlob(video: HTMLVideoElement, maxWidth: number, quality: number): Promise<Blob | null> {
  const canvas = drawFrame(video, maxWidth);
  if (!canvas) return Promise.resolve(null);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
}
