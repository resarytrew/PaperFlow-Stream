import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { CameraProfile } from "../api/types";
import { captureFrame, useCamera } from "../hooks/useCamera";
import { useApi } from "../lib";

type Step = 1 | 2 | 3 | 4;

export default function CalibrationPage() {
  const camera = useCamera();
  const videoRef = useRef<HTMLVideoElement>(null);
  const profile = useApi<CameraProfile | null>(() => api.get("/camera/profile"), []);

  const [step, setStep] = useState<Step>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [testResult, setTestResult] = useState<{ passed: boolean; warnings: string[]; sharpness: number; glare: number; resolution: number[] } | null>(null);
  const [backgroundOk, setBackgroundOk] = useState(false);
  const [detected, setDetected] = useState<{ quad: number[][]; preview: string | null; aspect_ratio: number; warnings: string[] } | null>(null);
  const [finalResult, setFinalResult] = useState<{ success: boolean; message: string; preview?: string; qrDetected?: boolean } | null>(null);

  useEffect(() => {
    if (videoRef.current && camera.stream) {
      videoRef.current.srcObject = camera.stream;
      videoRef.current.play().catch(() => undefined);
    }
  }, [camera.stream]);

  function grab(): string | null {
    const video = videoRef.current;
    if (!video) return null;
    return captureFrame(video, 1920, 0.9);
  }

  async function run<T>(fn: (image: string) => Promise<T>): Promise<T | null> {
    const image = grab();
    if (!image) {
      setError("Камера ещё не готова.");
      return null;
    }
    setBusy(true);
    setError(null);
    try {
      return await fn(image);
    } catch (e) {
      setError((e as Error).message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  const doTest = () =>
    run(async (image) => {
      const r = await api.post<typeof testResult>("/camera/test", { image });
      setTestResult(r);
      if (r?.passed) setStep(2);
      return r;
    });

  const doBackground = () =>
    run(async (image) => {
      await api.post("/camera/background", { image });
      setBackgroundOk(true);
      setStep(3);
      return null;
    });

  const doDetect = () =>
    run(async (image) => {
      const r = await api.post<{ found: boolean; quad: number[][] | null; aspect_ratio: number; preview: string | null; warnings: string[] }>(
        "/camera/detect-sheet",
        { image },
      );
      if (!r.found || !r.quad) {
        setError(r.warnings.join(" ") || "Бланк не найден.");
        return null;
      }
      setDetected({ quad: r.quad, preview: r.preview, aspect_ratio: r.aspect_ratio, warnings: r.warnings });
      return r;
    });

  async function saveProfile() {
    if (!detected) return;
    setBusy(true);
    setError(null);
    try {
      const track = camera.stream?.getVideoTracks()[0];
      const settings = track?.getSettings();
      await api.put("/camera/profile", {
        name: "default",
        device_id: settings?.deviceId ?? "",
        device_label: track?.label ?? "",
        width: settings?.width ?? 1920,
        height: settings?.height ?? 1080,
        work_area_polygon: detected.quad,
      });
      profile.refresh();
      setStep(4);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const doFinal = () =>
    run(async (image) => {
      const r = await api.post<typeof finalResult>("/camera/test-capture", { image });
      setFinalResult(r);
      return r;
    });

  return (
    <>
      <h1 className="page-title">Калибровка камеры</h1>
      {camera.error && <div className="error-box">{camera.error}</div>}
      {error && <div className="error-box">{error}</div>}
      {profile.data && (
        <div className="ok-box">
          Активный профиль: {profile.data.device_label || profile.data.name} · {profile.data.width}×{profile.data.height}
          {profile.data.background_reference_path ? " · фон сохранён" : " · фон не снят"}
        </div>
      )}

      <div className="scan-layout">
        <div className="video-stage">
          <video ref={videoRef} muted playsInline />
        </div>

        <div className="panel">
          <div className="row mb">
            <select value={camera.deviceId ?? ""} onChange={(e) => camera.start(e.target.value)} style={{ flex: 1 }}>
              {camera.devices.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || `Камера ${d.deviceId.slice(0, 6)}`}
                </option>
              ))}
            </select>
          </div>

          <h3 style={{ marginTop: 0 }}>Шаг {step} из 4</h3>

          {step === 1 && (
            <>
              <p className="muted">Проверка освещения, резкости и разрешения. Рабочая зона должна быть пустой и равномерно освещённой.</p>
              <button className="btn primary" onClick={doTest} disabled={busy}>
                {busy ? "Проверка…" : "Проверить камеру"}
              </button>
              {testResult && (
                <div className="mt">
                  <dl className="kv">
                    <dt>Разрешение</dt>
                    <dd>{testResult.resolution.join("×")}</dd>
                    <dt>Резкость</dt>
                    <dd>{(testResult.sharpness * 100).toFixed(0)}%</dd>
                    <dt>Блики</dt>
                    <dd>{(testResult.glare * 100).toFixed(1)}%</dd>
                  </dl>
                  {testResult.warnings.map((w) => (
                    <div key={w} className="error-box mt">{w}</div>
                  ))}
                  {!testResult.passed && (
                    <button className="btn mt" onClick={() => setStep(2)}>
                      Продолжить несмотря на замечания
                    </button>
                  )}
                </div>
              )}
            </>
          )}

          {step === 2 && (
            <>
              <p className="muted">Уберите всё из рабочей зоны и снимите опорный кадр фона — он повышает надёжность детекции листа.</p>
              <div className="row">
                <button className="btn primary" onClick={doBackground} disabled={busy}>
                  {busy ? "Сохранение…" : "Снять фон"}
                </button>
                <button className="btn" onClick={() => setStep(3)}>
                  Пропустить
                </button>
              </div>
              {backgroundOk && <div className="ok-box mt">Фон сохранён.</div>}
            </>
          )}

          {step === 3 && (
            <>
              <p className="muted">Положите один чистый бланк в центр рабочей зоны и нажмите «Найти бланк». Его контур станет рабочей областью.</p>
              <div className="row">
                <button className="btn primary" onClick={doDetect} disabled={busy}>
                  {busy ? "Поиск…" : "Найти бланк"}
                </button>
                {detected && (
                  <button className="btn success" onClick={saveProfile} disabled={busy}>
                    Сохранить профиль
                  </button>
                )}
              </div>
              {detected && (
                <div className="mt">
                  <div className="muted mb">Соотношение сторон: {detected.aspect_ratio.toFixed(3)}</div>
                  {detected.preview && <img src={detected.preview} alt="Выровненный бланк" style={{ maxWidth: "100%", borderRadius: 8 }} />}
                  {detected.warnings.map((w) => (
                    <div key={w} className="error-box mt">{w}</div>
                  ))}
                </div>
              )}
            </>
          )}

          {step === 4 && (
            <>
              <p className="muted">Контрольный захват: положите заполненный бланк с QR-кодом и проверьте, что всё читается.</p>
              <button className="btn primary" onClick={doFinal} disabled={busy}>
                {busy ? "Захват…" : "Тестовый захват"}
              </button>
              {finalResult && (
                <div className="mt">
                  <div className={finalResult.success && finalResult.qrDetected ? "ok-box" : "error-box"}>{finalResult.message}</div>
                  {finalResult.preview && <img src={finalResult.preview} alt="Тестовый захват" style={{ maxWidth: "100%", borderRadius: 8 }} />}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
