import { useCallback, useEffect, useState } from "react";

/** Tiny data-fetch hook: load(), loading, error, manual refresh. */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    fn()
      .then((d) => setData(d))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, loading, error, refresh, setData } as const;
}

export function fmtBytes(bytes: number): string {
  if (!bytes) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  const i = Math.min(Math.floor(Math.log2(bytes) / 10), units.length - 1);
  return `${(bytes / 2 ** (10 * i)).toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export const SCAN_STATUS_RU: Record<string, { label: string; color: string }> = {
  ok: { label: "OK", color: "green" },
  duplicate: { label: "Дубликат", color: "amber" },
  unidentified: { label: "Не распознан QR", color: "red" },
  low_quality: { label: "Низкое качество", color: "red" },
  rescan_required: { label: "Пересканировать", color: "amber" },
  deleted: { label: "Удалён", color: "gray" },
};

export const RECOG_STATUS_RU: Record<string, { label: string; color: string }> = {
  pending: { label: "В очереди", color: "gray" },
  processing: { label: "Распознаётся", color: "blue" },
  recognized: { label: "Распознано", color: "green" },
  needs_review: { label: "Нужна проверка", color: "amber" },
  blank: { label: "Пустой ответ", color: "gray" },
  failed: { label: "Ошибка OCR", color: "red" },
};

export const SESSION_STATUS_RU: Record<string, { label: string; color: string }> = {
  draft: { label: "Черновик", color: "gray" },
  scanning: { label: "Сканирование", color: "blue" },
  paused: { label: "Пауза", color: "amber" },
  review: { label: "Проверка", color: "amber" },
  completed: { label: "Завершена", color: "green" },
};

export function Badge({ map, value }: { map: Record<string, { label: string; color: string }>; value: string }) {
  const info = map[value] ?? { label: value, color: "gray" };
  return <span className={`badge ${info.color}`}>{info.label}</span>;
}
