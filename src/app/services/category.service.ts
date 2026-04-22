import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class CategoryService {
  private selectedSquadSource = new BehaviorSubject<boolean[] | null>(null);
  selectedSquad$ = this.selectedSquadSource.asObservable();

  nodes: any[] = [];
  squadDict: { [key: string]: number } = {};

  COLORS = [
    '#6A9BB6',
    '#fac858',
    '#ee6666',
    '#3ba272',
    '#fc8452',
    '#5470c6',
    '#9a60b4',
    '#ea7ccc',
    '#91cc75',
    '#73c0de',
  ];

  setSelectedCategory(selectedCategories: boolean[]): void {
    this.selectedSquadSource.next(selectedCategories);
  }
  getSelectedCategory() {
    return this.selectedSquadSource.value;
  }
}
