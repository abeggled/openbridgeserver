import mqtt, { type MqttClient as MqttJsClient } from 'mqtt';

/** Matches obs/core/mqtt_client.py's build_payload() JSON shape on dp/{uuid}/value. */
export interface DataPointValuePayload {
  v: unknown;
  u: string | null;
  t: string;
  q: string;
}

export type ValueUpdateHandler = (dataPointId: string, payload: DataPointValuePayload) => void;

export class ObsMqttClient {
  private client?: MqttJsClient;
  private readonly handlers = new Map<string, ValueUpdateHandler>();

  constructor(
    private readonly host: string,
    private readonly port: number,
    private readonly username?: string,
    private readonly password?: string,
  ) {}

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const client = mqtt.connect({
        host: this.host,
        port: this.port,
        username: this.username,
        password: this.password,
        protocol: 'mqtt',
      });
      client.once('connect', () => resolve());
      client.once('error', (err) => reject(err));
      client.on('message', (topic, payload) => this.handleMessage(topic, payload));
      this.client = client;
    });
  }

  /** Subscribes to dp/{dataPointId}/value and invokes `handler` on every published update. */
  subscribeToValue(dataPointId: string, handler: ValueUpdateHandler): void {
    if (!this.client) throw new Error('matterbridge-obs: MQTT client not connected');
    const topic = `dp/${dataPointId}/value`;
    this.handlers.set(topic, handler);
    this.client.subscribe(topic);
  }

  /** Publishes a write command to dp/{dataPointId}/set — routed by OBS's WriteRouter. */
  publishSet(dataPointId: string, value: string): void {
    if (!this.client) throw new Error('matterbridge-obs: MQTT client not connected');
    this.client.publish(`dp/${dataPointId}/set`, value);
  }

  async disconnect(): Promise<void> {
    await this.client?.endAsync();
  }

  private handleMessage(topic: string, payload: Buffer): void {
    const handler = this.handlers.get(topic);
    if (!handler) return;
    const dataPointId = topic.split('/')[1] ?? '';
    try {
      const parsed = JSON.parse(payload.toString()) as DataPointValuePayload;
      handler(dataPointId, parsed);
    } catch {
      // malformed/non-JSON payload on a dp/*/value topic — ignore, next update will self-correct
    }
  }
}
