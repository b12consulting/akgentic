import { Injectable, inject } from '@angular/core';
import { Clipboard } from '@angular/cdk/clipboard';
import { MessageService } from 'primeng/api';

@Injectable({
  providedIn: 'root',
})
export class UtilService {
  clipboard: Clipboard = inject(Clipboard);
  messageService: MessageService = inject(MessageService);

  /**
   * Formats the given content into a string representation.
   *
   * @param content - The content to format. It can be a string, boolean, number, or an object.
   * @returns The formatted string representation of the content.
   */
  formatText(
    content: string | boolean | number | { [key: string]: any }
  ): string {
    if (content === undefined || content === null) {
      return '';
    } else if (typeof content === 'string') {
      try {
        const parsed = JSON.parse(content);
        return this.formatText(parsed);
      } catch (e) {
        return content.replace(/\n/g, '<br />');
      }
    } else if (typeof content === 'boolean') {
      return content ? 'True' : 'False';
    } else if (typeof content === 'number') {
      return content.toString();
    }

    return Object.keys(content)
      .map((key) => {
        return `<b>${key.toUpperCase()}</b><br /><br />${this.formatText(
          content[key]
        )}`;
      })
      .join('<br /><br />');
  }

  formatJSON(
    content: string | boolean | number | { [key: string]: any }
  ): string {
    if (typeof content === 'object') {
      return JSON.stringify(content, null, 2);
    }
    return content.toString();
  }

  copyToClipboard(content: string) {
    // Copy to clipboard
    this.clipboard.copy(content);
    this.messageService.add({
      severity: 'success',
      summary: 'Content copied to the clipbaord',
    });
  }
}
