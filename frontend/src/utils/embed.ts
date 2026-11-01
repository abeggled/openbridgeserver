/**
 * Eingebettete Visu (`?embed=1`): so lädt das Alarm-Overlay eine Alarm-Seite in einen Rahmen.
 *
 * Dort fehlen Kopfzeile, Alarm-Overlay und Rücksprung — sonst zeigte der Rahmen
 * seinerseits Alarme an. Der Modus bleibt für die Lebensdauer des Rahmens
 * erhalten, auch wenn darin weiternavigiert wird: gemerkt wird er in
 * `window.name`, das zum Rahmen gehört. `sessionStorage` ginge nicht — ein
 * Rahmen derselben Herkunft teilt ihn mit dem Hauptfenster, das sich dann nach
 * dem Neuladen selbst für eingebettet hielte. Bewusst nicht über `window.top`
 * erkannt: eine Visu, die jemand in ein anderes Dashboard einbettet, soll
 * unverändert bleiben.
 */
const FRAME_NAME = 'obs-visu-embed'

export function isEmbedded(): boolean {
  if (new URLSearchParams(window.location.search).get('embed') === '1') window.name = FRAME_NAME
  return window.name === FRAME_NAME
}
