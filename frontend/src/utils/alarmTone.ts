/**
 * Kurzer Signalton für Visu-Alarme, im Browser erzeugt (keine Audiodatei nötig).
 * Browser erlauben Ton erst nach einer ersten Bedienung der Seite; vorher
 * bleibt es still, ohne Fehler.
 */
let context: AudioContext | null = null

export function playAlarmTone(): void {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctx) return
    context ??= new Ctx()
    const start = context.currentTime
    for (const offset of [0, 0.35, 0.7]) {
      const osc = context.createOscillator()
      const gain = context.createGain()
      osc.type = 'sine'
      osc.frequency.value = 880
      gain.gain.setValueAtTime(0.0001, start + offset)
      gain.gain.exponentialRampToValueAtTime(0.3, start + offset + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.25)
      osc.connect(gain).connect(context.destination)
      osc.start(start + offset)
      osc.stop(start + offset + 0.3)
    }
  } catch {
    // Kein Ton möglich (z. B. vor der ersten Bedienung) — die Anzeige reicht.
  }
}
