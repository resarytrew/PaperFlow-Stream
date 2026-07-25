/** Audio feedback for the scanning flow.
 *
 * The teacher feeds paper without looking at the screen, so accept/reject
 * must be audible. WebAudio tones — no asset files, works offline.
 */

let ctx: AudioContext | null = null;

function ensureContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (!ctx) {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    ctx = new Ctor();
  }
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

function tone(frequency: number, startAt: number, duration: number, volume: number, type: OscillatorType = "sine") {
  const audio = ensureContext();
  if (!audio) return;
  const osc = audio.createOscillator();
  const gain = audio.createGain();
  osc.type = type;
  osc.frequency.value = frequency;
  const t0 = audio.currentTime + startAt;
  gain.gain.setValueAtTime(0, t0);
  gain.gain.linearRampToValueAtTime(volume, t0 + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
  osc.connect(gain).connect(audio.destination);
  osc.start(t0);
  osc.stop(t0 + duration + 0.05);
}

/** Short rising major chirp — sheet accepted. */
export function playSuccess(volume = 0.22) {
  tone(660, 0, 0.09, volume);
  tone(880, 0.09, 0.14, volume);
}

/** Low double buzz — sheet rejected, repeat the feed. */
export function playWarning(volume = 0.26) {
  tone(220, 0, 0.16, volume, "square");
  tone(220, 0.22, 0.16, volume, "square");
}

/** Single neutral tick — connection restored. */
export function playReconnected(volume = 0.15) {
  tone(520, 0, 0.08, volume);
}

/** Unlock audio on the first user gesture (browsers require it). */
export function unlockAudio() {
  ensureContext();
}
