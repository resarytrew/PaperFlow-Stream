import { useCallback, useEffect, useRef, useState } from "react";

interface Props {
  /** Frozen frame (data URL) the quad was detected on. */
  image: string;
  /** Quad in image pixel coordinates, 4 points. */
  quad: number[][];
  onChange: (quad: number[][]) => void;
  maxHeight?: number;
}

const HANDLE_RADIUS = 14;

/** Canvas with four draggable corner handles over a frozen camera frame.
 *  Lets the teacher fix the work area when auto-detection gets it wrong. */
export default function QuadEditor({ image, quad, onChange, maxHeight = 420 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [loaded, setLoaded] = useState(false);
  const dragIndexRef = useRef<number>(-1);
  const quadRef = useRef(quad);
  quadRef.current = quad;

  // Load the frozen frame once.
  useEffect(() => {
    const img = new Image();
    img.onload = () => {
      imgRef.current = img;
      setLoaded(true);
    };
    img.src = image;
    return () => {
      imgRef.current = null;
      setLoaded(false);
    };
  }, [image]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return;
    const scale = Math.min(1, maxHeight / img.height, 560 / img.width);
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    const pts = quadRef.current.map(([x, y]) => [x * scale, y * scale] as const);

    // dim everything outside the quad
    ctx.save();
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.beginPath();
    ctx.rect(0, 0, canvas.width, canvas.height);
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = pts.length - 1; i >= 0; i--) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    ctx.fill("evenodd");
    ctx.restore();

    ctx.strokeStyle = "#3b82f6";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.stroke();

    pts.forEach(([x, y], i) => {
      ctx.fillStyle = dragIndexRef.current === i ? "#f59e0b" : "#3b82f6";
      ctx.beginPath();
      ctx.arc(x, y, 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.stroke();
    });
  }, [maxHeight]);

  useEffect(() => {
    draw();
  }, [draw, loaded, quad]);

  function eventPoint(event: React.PointerEvent): [number, number, number] {
    const canvas = canvasRef.current!;
    const img = imgRef.current!;
    const rect = canvas.getBoundingClientRect();
    const scale = canvas.width / img.width;
    const cx = ((event.clientX - rect.left) * canvas.width) / rect.width;
    const cy = ((event.clientY - rect.top) * canvas.height) / rect.height;
    return [cx / scale, cy / scale, scale];
  }

  function onPointerDown(event: React.PointerEvent) {
    if (!imgRef.current) return;
    const [x, y, scale] = eventPoint(event);
    const hit = quadRef.current.findIndex(
      ([px, py]) => Math.hypot((px - x) * scale, (py - y) * scale) <= HANDLE_RADIUS,
    );
    if (hit >= 0) {
      dragIndexRef.current = hit;
      (event.target as HTMLElement).setPointerCapture(event.pointerId);
      draw();
    }
  }

  function onPointerMove(event: React.PointerEvent) {
    const index = dragIndexRef.current;
    if (index < 0 || !imgRef.current) return;
    const img = imgRef.current;
    const [x, y] = eventPoint(event);
    const next = quadRef.current.map((p, i) =>
      i === index ? [Math.min(Math.max(x, 0), img.width), Math.min(Math.max(y, 0), img.height)] : p,
    );
    onChange(next);
  }

  function onPointerUp(event: React.PointerEvent) {
    if (dragIndexRef.current >= 0) {
      dragIndexRef.current = -1;
      (event.target as HTMLElement).releasePointerCapture(event.pointerId);
      draw();
    }
  }

  return (
    <canvas
      ref={canvasRef}
      style={{ maxWidth: "100%", borderRadius: 8, cursor: "crosshair", touchAction: "none" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    />
  );
}
