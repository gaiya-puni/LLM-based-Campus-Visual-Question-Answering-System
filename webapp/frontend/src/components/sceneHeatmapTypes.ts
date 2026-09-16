export interface HeatmapPayload {
  available: boolean;
  scene: string;
  sceneName: string;
  season?: string | null;
  seasonLabel?: string | null;
  campus: string;
  grid: {
    width: number; height: number; bounds: [number, number, number, number];
    rowOrder: string; values: number[]; maxValue: number; noData: number;
  };
  places: {
    name: string; number: string; lng: number; lat: number; campus: string;
    kind: string; rank: number; score: number; reason: string; plants: string[];
    access_verified?: boolean;
  }[];
}
