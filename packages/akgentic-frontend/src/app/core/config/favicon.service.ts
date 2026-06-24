import { DOCUMENT } from '@angular/common';
import { Inject, Injectable } from '@angular/core';

@Injectable({
  providedIn: 'root', // Makes the service a singleton available application-wide
})
export class FaviconService {
  constructor(@Inject(DOCUMENT) private document: Document) {}

  setFavicon(favicon: string): void {
    const linkElement = this.document.getElementById(
      'appIcon'
    ) as HTMLLinkElement | null;

    if (linkElement) {
      linkElement.href = favicon;
    }
  }
}
