export interface ObsDataPoint {
  id: string;
  name: string;
  data_type: string;
  unit: string | null;
  tags: string[];
  mqtt_topic: string;
}

interface DataPointPage {
  items: ObsDataPoint[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

const PAGE_SIZE = 10000;

export class ObsApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
  ) {}

  /** Fetches every DataPoint from OBS (GET /api/v1/datapoints/, paginated). */
  async fetchAllDataPoints(): Promise<ObsDataPoint[]> {
    const items: ObsDataPoint[] = [];
    let page = 0;
    let pages = 1;

    do {
      const url = `${this.baseUrl}/api/v1/datapoints/?page=${page}&size=${PAGE_SIZE}`;
      const response = await fetch(url, {
        headers: { 'X-API-Key': this.apiKey },
      });
      if (!response.ok) {
        throw new Error(`matterbridge-obs: GET ${url} failed with ${response.status} ${response.statusText}`);
      }
      const body = (await response.json()) as DataPointPage;
      items.push(...body.items);
      pages = body.pages;
      page += 1;
    } while (page < pages);

    return items;
  }
}
