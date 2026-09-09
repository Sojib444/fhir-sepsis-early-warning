import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import {
  ApiService,
  ShapContribution,
  TrajectoryResponse,
} from './api';

@Component({
  selector: 'app-patient-detail',
  imports: [RouterLink],
  templateUrl: './patient-detail.html',
  styleUrls: ['./patient-detail.css'],
})
export class PatientDetailComponent {
  protected readonly W = 760;
  protected readonly H = 280;
  protected readonly PL = 46;
  protected readonly PR = 16;
  protected readonly PT = 14;
  protected readonly PB = 34;
  protected readonly PLOT_W = this.W - this.PL - this.PR;
  protected readonly PLOT_H = this.H - this.PT - this.PB;

  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly patientId = this.route.snapshot.paramMap.get('id') ?? '';

  readonly data = signal<TrajectoryResponse | null>(null);
  readonly error = signal<string | null>(null);
  readonly loading = signal(false);
  readonly selectedHour = signal<number | null>(null);

  protected readonly maxHour = computed(() => {
    const hours = this.data()?.hours ?? [];
    return hours.length === 0 ? 0 : hours[hours.length - 1].hour;
  });

  constructor() {
    void this.load();
  }

  private async load(hour?: number) {
    this.loading.set(true);
    this.error.set(null);
    try {
      const response = await firstValueFrom(this.api.trajectory(this.patientId, hour));
      this.data.set(response);
      this.selectedHour.set(response.shapHour);
    } catch {
      this.error.set('Could not score this patient (unreachable services or no observations).');
    } finally {
      this.loading.set(false);
    }
  }

  protected selectHour(hour: number) {
    this.selectedHour.set(hour);
    void this.load(hour);
  }

  // --- plot geometry ---------------------------------------------------------

  protected x(hour: number): number {
    const max = Math.max(1, this.maxHour());
    return this.PL + (hour / max) * this.PLOT_W;
  }

  protected y(risk: number): number {
    return this.PT + (1 - risk) * this.PLOT_H;
  }

  protected polyline(): string {
    const points = (this.data()?.hours ?? [])
      .map((p) => `${this.x(p.hour).toFixed(1)},${this.y(p.risk).toFixed(1)}`)
      .join(' ');
    return points;
  }

  protected thresholdY(): number {
    return this.y(this.data()?.threshold ?? 0);
  }

  protected onsetX(): number {
    const onset = this.data()?.onsetHour;
    return onset === null || onset === undefined ? NaN : this.x(onset);
  }

  protected chords(): string {
    return [
      `M ${this.PL} ${this.PT}`,
      `L ${this.W - this.PR} ${this.PT}`,
      `L ${this.W - this.PR} ${this.H - this.PB}`,
      `L ${this.PL} ${this.H - this.PB}`,
      'Z',
    ].join(' ');
  }

  protected hourTicks(): number[] {
    const max = this.maxHour();
    const step = Math.max(1, Math.ceil(max / 8));
    const ticks: number[] = [];
    for (let h = 0; h <= max; h += step) ticks.push(h);
    return ticks;
  }

  protected riskTicks(): number[] {
    return [0, 0.25, 0.5, 0.75, 1];
  }

  // --- SHAP waterfall ---------------------------------------------------------

  protected waterfall(): ShapContribution[] {
    return [...(this.data()?.shap ?? [])]
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
      .slice(0, 10);
  }

  protected barMax(items: ShapContribution[]): number {
    const max = items.reduce((m, c) => Math.max(m, Math.abs(c.value)), 0);
    return max > 0 ? max : 1;
  }

  protected pct(x: number): string {
    return `${(x * 100).toFixed(1)}%`;
  }

  protected fmt(v: number): string {
    return v.toFixed(3);
  }
}