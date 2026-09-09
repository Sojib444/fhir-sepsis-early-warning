import { Component, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService, SweepResponse, SweepRow } from './api';

@Component({
  selector: 'app-sweep',
  templateUrl: './sweep.html',
  styles: [
    `
      .row {
        align-items: center;
        display: grid;
        gap: 0.75rem;
        grid-template-columns: 9rem 1fr 7rem;
        padding: 0.4rem 0;
      }
      .label {
        color: #444;
      }
      .track {
        background: #f2f2f2;
        border-radius: 4px;
        height: 16px;
        width: 100%;
      }
      .bar {
        background: #1f4e79;
        border-radius: 4px;
        height: 100%;
      }
      .value {
        font-weight: 600;
        text-align: right;
      }
      input[type='range'] {
        display: block;
        max-width: 720px;
        width: 100%;
      }
      .operating {
        color: #b3403a;
        font-size: 0.9rem;
      }
      .error {
        color: #a00;
      }
    `,
  ],
})
export class SweepComponent {
  private readonly api = inject(ApiService);

  readonly response = signal<SweepResponse | null>(null);
  readonly error = signal<string | null>(null);
  readonly selected = signal(0.05);

  protected readonly min = computed(() => this.response()?.rows[0]?.threshold ?? 0);
  protected readonly max = computed(() => {
    const rows = this.response()?.rows ?? [];
    return rows.length ? rows[rows.length - 1].threshold : 1;
  });

  protected readonly current = computed<SweepRow | null>(() => {
    const rows = this.response()?.rows ?? [];
    if (rows.length === 0) return null;
    const target = this.selected();
    let best = rows[0];
    for (const r of rows) {
      if (Math.abs(r.threshold - target) < Math.abs(best.threshold - target)) best = r;
    }
    return best;
  });

  constructor() {
    void this.load();
  }

  private async load() {
    this.error.set(null);
    try {
      const response = await firstValueFrom(this.api.sweep());
      this.response.set(response);
      this.selected.set(response.operating_threshold);
    } catch {
      this.error.set(
        'Sweep data unavailable — the CDS service needs the precomputed rigor.json mounted.',
      );
    }
  }

  protected onInput(event: Event) {
    this.selected.set(Number((event.target as HTMLInputElement).value));
  }

  protected pct(x: number): string {
    return `${(x * 100).toFixed(1)}%`;
  }

  protected alerts(x: number): string {
    return x === 0 ? '0' : x.toFixed(2);
  }
}