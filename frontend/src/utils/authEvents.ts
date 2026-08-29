/**
 * Auth-Events der Visu
 *
 * Gegenstück zu `gui/src/utils/authEvents.js` in der Admin-GUI: Nach einem
 * erfolgreichen Token-Refresh muss der WebSocket neu verbunden werden, weil der
 * JWT im Subprotokoll (`obs.jwt.<token>`) steckt und beim Handshake gebunden
 * wird — ein erneuerter Token erreicht eine bestehende Verbindung nicht.
 */

export const AUTH_TOKEN_REFRESHED_EVENT = 'visu:auth-token-refreshed'

export function notifyAuthTokenRefreshed(): void {
  window.dispatchEvent(new Event(AUTH_TOKEN_REFRESHED_EVENT))
}
