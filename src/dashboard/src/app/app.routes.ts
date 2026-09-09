import { Routes } from '@angular/router';
import { PatientsComponent } from './patients';
import { PatientDetailComponent } from './patient-detail';
import { SweepComponent } from './sweep';

export const routes: Routes = [
  { path: '', redirectTo: 'patients', pathMatch: 'full' },
  { path: 'patients', component: PatientsComponent },
  { path: 'patient/:id', component: PatientDetailComponent },
  { path: 'sweep', component: SweepComponent },
];