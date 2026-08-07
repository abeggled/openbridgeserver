import { afterEach, describe, expect, it, vi } from 'vitest';

import { ObsApiClient, type ObsDataPoint } from '../src/obsApiClient.js';

function page(items: ObsDataPoint[], pageNum: number, pages: number) {
  return { items, total: items.length, page: pageNum, size: 10000, pages };
}

function dataPoint(id: string): ObsDataPoint {
  return { id, name: `dp-${id}`, data_type: 'BOOLEAN', unit: null, tags: [], mqtt_topic: `dp/${id}/value` };
}

describe('ObsApiClient.fetchAllDataPoints', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches a single page and sends the API key header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => page([dataPoint('a'), dataPoint('b')], 0, 1),
    });
    vi.stubGlobal('fetch', fetchMock);

    const client = new ObsApiClient('http://localhost:8080', 'obs_test_key');
    const result = await client.fetchAllDataPoints();

    expect(result.map((dp) => dp.id)).toEqual(['a', 'b']);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://localhost:8080/api/v1/datapoints/?page=0&size=10000');
    expect(init.headers).toEqual({ 'X-API-Key': 'obs_test_key' });
  });

  it('follows pagination until all pages are fetched', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, statusText: 'OK', json: async () => page([dataPoint('a')], 0, 2) })
      .mockResolvedValueOnce({ ok: true, status: 200, statusText: 'OK', json: async () => page([dataPoint('b')], 1, 2) });
    vi.stubGlobal('fetch', fetchMock);

    const client = new ObsApiClient('http://localhost:8080', 'obs_test_key');
    const result = await client.fetchAllDataPoints();

    expect(result.map((dp) => dp.id)).toEqual(['a', 'b']);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('throws a descriptive error on a non-ok response', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401, statusText: 'Unauthorized' });
    vi.stubGlobal('fetch', fetchMock);

    const client = new ObsApiClient('http://localhost:8080', 'bad_key');

    await expect(client.fetchAllDataPoints()).rejects.toThrow(/401 Unauthorized/);
  });
});
