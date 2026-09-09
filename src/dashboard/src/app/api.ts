import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';

export type AlertIndicator = 'info' | 'warning' | 'critical';

export interface PatientRow {
  patientId: string;
  risk: number;
  threshold: number;
  indicator: AlertIndicator;
}

export interface PatientsResponse {
  patients: PatientRow[];
}

export interface RiskPoint {
  hour: number;
  risk: number;
}

export interface ShapContribution {
  feature: string;
  value: number;
}

export interface TrajectoryResponse {
  patientId: string;
  threshold: number;
  onsetHour: number | null;
  hours: RiskPoint[];
  shap: ShapContribution[];
  shapHour: number;
}

export interface SweepRow {
  threshold: number;
  sensitivity: number;
  ppv: number;
  alerts_per_100_icu_days: number;
}

export interface SweepResponse {
  rows: SweepRow[];
  operating_threshold: number;
  operating: SweepRow;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  patients(limit = 50) {
    return this.http.get<PatientsResponse>(`/api/patients?limit=${limit}`);
  }

  trajectory(patientId: string, hour?: number) {
    return this.http.post<TrajectoryResponse>('/api/sepsis-risk/trajectory', {
      patientId,
      hour,
    });
  }

  sweep() {
    return this.http.get<SweepResponse>('/api/sepsis-risk/sweep');
  }
}