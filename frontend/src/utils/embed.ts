/**
 * Eingebettete Visu (`?embed=1`): so lädt das Alarm-Overlay eine Alarm-Seite in einen Rahmen.
 *
 * Dort fehlen Kopfzeile, Alarm-Overlay und Rücksprung — sonst zeigte der Rahmen
 * seinerseits Alarme an. Der Modus bleibt für die Lebensdauer des Rahmens
 * erhalten (sessionStorage gilt pro Rahmen), auch wenn darin weiternavigiert
 * wird. Bewusst nicht über `window.top` erkannt: eine Visu, die jemand in ein
 * anderes Dashboard einbettet, soll unverändert bleiben.
 */
const KEY = 'visu_embed'

export function isEmbedded(): boolean {
  try {
    if (new URLSearchParams(window.location.search).get('embed') === '1') sessionStorage.setItem(KEY, '1')
    return sessionStorage.getItem(KEY) === '1'
  } catch {
    return new URLSearchParams(window.location.search).get('embed') === '1'
  }
}
