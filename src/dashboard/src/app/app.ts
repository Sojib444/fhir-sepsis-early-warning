import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Disclaimer } from './disclaimer';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Disclaimer],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {}