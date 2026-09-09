import { Component } from '@angular/core';

@Component({
  selector: 'app-disclaimer',
  template: `
    <div class="disclaimer">
      Research prototype — not validated for clinical use. Not a medical device.
    </div>
  `,
  styles: [
    `
      .disclaimer {
        background: #fff8e1;
        border-bottom: 1px solid #e6d9a8;
        color: #6b5d0e;
        font-size: 0.85rem;
        padding: 0.4rem 1rem;
        text-align: center;
      }
    `,
  ],
})
export class Disclaimer {}