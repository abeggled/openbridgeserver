import { EventEmitter } from 'node:events';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ObsMqttClient } from '../src/mqttClient.js';

class FakeMqttClient extends EventEmitter {
  subscribe = vi.fn();
  publish = vi.fn();
  endAsync = vi.fn().mockResolvedValue(undefined);
}

let fakeClient: FakeMqttClient;

vi.mock('mqtt', () => ({
  default: {
    connect: vi.fn(() => fakeClient),
  },
}));

describe('ObsMqttClient', () => {
  beforeEach(() => {
    fakeClient = new FakeMqttClient();
  });

  it('resolves connect() once the underlying client emits "connect"', async () => {
    const client = new ObsMqttClient('localhost', 1883);
    const connected = client.connect();
    fakeClient.emit('connect');
    await expect(connected).resolves.toBeUndefined();
  });

  it('rejects connect() if the underlying client emits "error" first', async () => {
    const client = new ObsMqttClient('localhost', 1883);
    const connected = client.connect();
    fakeClient.emit('error', new Error('boom'));
    await expect(connected).rejects.toThrow('boom');
  });

  it('subscribeToValue subscribes to dp/{id}/value and dispatches parsed payloads', async () => {
    const client = new ObsMqttClient('localhost', 1883);
    const connected = client.connect();
    fakeClient.emit('connect');
    await connected;

    const handler = vi.fn();
    client.subscribeToValue('abc', handler);
    expect(fakeClient.subscribe).toHaveBeenCalledWith('dp/abc/value');

    fakeClient.emit('message', 'dp/abc/value', Buffer.from(JSON.stringify({ v: 21.5, u: '°C', t: '2026-01-01T00:00:00.000Z', q: 'good' })));
    expect(handler).toHaveBeenCalledWith('abc', { v: 21.5, u: '°C', t: '2026-01-01T00:00:00.000Z', q: 'good' });
  });

  it('ignores messages on topics with no registered handler', async () => {
    const client = new ObsMqttClient('localhost', 1883);
    const connected = client.connect();
    fakeClient.emit('connect');
    await connected;

    expect(() => fakeClient.emit('message', 'dp/unknown/value', Buffer.from('{}'))).not.toThrow();
  });

  it('ignores malformed (non-JSON) payloads instead of throwing', async () => {
    const client = new ObsMqttClient('localhost', 1883);
    const connected = client.connect();
    fakeClient.emit('connect');
    await connected;

    const handler = vi.fn();
    client.subscribeToValue('abc', handler);
    expect(() => fakeClient.emit('message', 'dp/abc/value', Buffer.from('not json'))).not.toThrow();
    expect(handler).not.toHaveBeenCalled();
  });

  it('publishSet publishes to dp/{id}/set', async () => {
    const client = new ObsMqttClient('localhost', 1883);
    const connected = client.connect();
    fakeClient.emit('connect');
    await connected;

    client.publishSet('abc', 'true');
    expect(fakeClient.publish).toHaveBeenCalledWith('dp/abc/set', 'true');
  });

  it('subscribeToValue throws if called before connect()', () => {
    const client = new ObsMqttClient('localhost', 1883);
    expect(() => client.subscribeToValue('abc', vi.fn())).toThrow(/not connected/);
  });

  it('publishSet throws if called before connect()', () => {
    const client = new ObsMqttClient('localhost', 1883);
    expect(() => client.publishSet('abc', 'true')).toThrow(/not connected/);
  });

  it('disconnect() ends the underlying client', async () => {
    const client = new ObsMqttClient('localhost', 1883);
    const connected = client.connect();
    fakeClient.emit('connect');
    await connected;

    await client.disconnect();
    expect(fakeClient.endAsync).toHaveBeenCalled();
  });

  it('disconnect() is a no-op if never connected', async () => {
    const client = new ObsMqttClient('localhost', 1883);
    await expect(client.disconnect()).resolves.toBeUndefined();
  });
});
