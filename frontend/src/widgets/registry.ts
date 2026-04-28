/**
 * WidgetRegistry — Self-Registering Widget-System
 *
 * Widgets registrieren sich selbst beim Import ihrer index.ts.
 * Der Editor, Viewer und die Palette lesen ausschliesslich aus dieser Registry.
 */

import type { Component } from 'vue'

export interface WidgetDefinition {
  /** Interner Typ-Schlüssel, z.B. "ValueDisplay" */
  type: string
  /** Label in der Widget-Palette */
  label: string
  /** Emoji oder SVG-String für die Palette */
  icon: string
  /** Gruppe in der Widget-Palette (z.B. "Steuerung", "Anzeige") */
  group?: string
  /** Mindestbreite im Grid (in Spalten) */
  minW: number
  /** Mindesthöhe im Grid (in Zeilen) */
  minH: number
  /** Standardbreite beim Einfügen */
  defaultW: number
  /** Standardhöhe beim Einfügen */
  defaultH: number
  /** Vue-Komponente für den Viewer */
  component: Component
  /** Vue-Komponente für das Editor-Formular */
  configComponent: Component
  /** Standard-Config, die beim Einfügen verwendet wird */
  defaultConfig: Record<string, unknown>
  /** Kompatible DataPoint-Typen. ["*"] = alle */
  compatibleTypes: string[]
  /** Unterstützt separaten Status-Datenpunkt (z.B. Toggle, Slider) */
  supportsStatusDatapoint?: boolean
  /** Kein eigener DataPoint nötig (z.B. Link, Widget-Referenz) */
  noDatapoint?: boolean
  /** Optionale Funktion: gibt zusätzliche Datenpunkt-IDs aus der Config zurück (z.B. Info-Widget) */
  getExtraDatapointIds?: (config: Record<string, unknown>) => string[]
}

class Registry {
  private _widgets = new Map<string, WidgetDefinition>()

  register(def: WidgetDefinition): void {
    if (this._widgets.has(def.type)) {
      console.warn(`[WidgetRegistry] Widget "${def.type}" bereits registriert — wird überschrieben.`)
    }
    this._widgets.set(def.type, def)
  }

  get(type: string): WidgetDefinition | undefined {
    return this._widgets.get(type)
  }

  all(): WidgetDefinition[] {
    return Array.from(this._widgets.values())
  }
}

export const WidgetRegistry = new Registry()
