import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { ApiService, PatientRow } from './api';

@Component({
  selector: 'app-patients',
  templateUrl: './patients.html',
  styles: [
    `
      table {
        border-collapse: collapse;
        width: 100%;
        max-width: 720px;
      }
      th,
      td {
        border-bottom: 1px solid #e2e2e2;
        padding: 0.45rem 0.6rem;
        text-align: left;
      }
      tr[data-clickable] {
        cursor: pointer;
      }
      tr[data-clickable]:hover {
        background: #f2f6ff;
      }
      .chip {
        border-radius: 999px;
        display: inline-block;
        font-size: 0.75rem;
        padding: 0.1rem 0.6rem;
      }
      .chip.info {
        background: #e8eef7;
        color: #1f4e79;
      }
      .chip.warning {
        background: #fdf0d9;
        color: #8a5b00;
      }
      .chip.critical {
        background: #f9dede;
        color: #a00;
      }
      .low {
        color: #2d7d46;
      }
      .high {
        color: #b3403a;
        font-weight: 600;
      }
      .error {
        color: #a00;
      }
    `,
  ],
})
export class PatientsComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly rows = signal<PatientRow[]>([]);
  readonly error = signal<string | null>(null);

  constructor() {
    void this.load();
  }

  protected async load() {
    this.error.set(null);
    try {
      const response = await firstValueFrom(this.api.patients(50));
      this.rows.set(
        [...response.patients].sort((a, b) => b.risk - a.risk),
      );
    } catch {
      this.error.set('The CDS service is unreachable. Is `docker compose up` running?');
    }
  }

  protected pct(x: number): string {
    return `${(x * 100).toFixed(1)}%`;
  }

  protected open(patientId: string) {
    void this.router.navigate(['/patient', patientId]);
  }
}