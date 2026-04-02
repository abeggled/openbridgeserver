// ── VisuNode ──────────────────────────────────────────────────────────────────

export type NodeType = 'LOCATION' | 'PAGE'
export type AccessLevel = 'readonly' | 'public' | 'protected' | 'private'

export interface VisuNode {
  id: string
  parent_id: string | null
  name: string
  type: NodeType
  order: number
  icon: string | null
  access: AccessLevel | null   // null = von Elternknoten erben
  /** Nur beim Schreiben (PATCH/POST): Klartext-PIN, wird backend-seitig gehasht.
   *  Wird vom Backend niemals zurückgegeben. */
  access_pin?: string | null
  page_config: PageConfig | null
  created_at: string
  updated_at: string
}

// ── PageConfig ────────────────────────────────────────────────────────────────

export interface PageConfig {
  grid_cols: number
  grid_row_height: number
  /** Feste Zellbreite in Pixeln — identisch in Editor und Viewer (WYSIWYG) */
  grid_cell_width: number
  background: string | null
  widgets: WidgetInstance[]
}

export interface WidgetInstance {
  id: string
  name: string
  type: string
  datapoint_id: string | null
  /** Optionaler separater Status-Datenpunkt (für Widgets die schreiben und lesen) */
  status_datapoint_id: string | null
  x: number
  y: number
  w: number
  h: number
  config: Record<string, unknown>
}

// ── DataPoint ─────────────────────────────────────────────────────────────────

export interface DataPoint {
  id: string
  name: string
  data_type: string
  unit: string | null
  tags: string[]
  mqtt_topic: string
  mqtt_alias: string | null
  created_at: string
  updated_at: string
}

export interface DataPointValue {
  id: string
  v: unknown
  u: string | null
  t: string
  q: 'good' | 'bad' | 'uncertain'
}

// ── Widget-System ─────────────────────────────────────────────────────────────

export interface WidgetProps {
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
  readonly?: boolean
}

// ── API ───────────────────────────────────────────────────────────────────────

export interface ApiError {
  detail: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  size: number
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export interface LoginResponse {
  access_token: string
  token_type: string
}

export interface PinAuthResponse {
  session_token: string
  expires_in: number
}
