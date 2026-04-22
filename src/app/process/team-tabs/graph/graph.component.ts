import { CommonModule } from '@angular/common';
import { Component, inject, NgZone } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { TabViewModule } from 'primeng/tabview';
import { TextareaModule } from 'primeng/textarea';

import { Subscription, combineLatest } from 'rxjs';

import { GraphChart } from 'echarts/charts';
import {
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { EChartsCoreOption } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { NgxEchartsDirective, provideEchartsCore } from 'ngx-echarts';

import { AkgentService } from '../../../services/akgent.service';
import { ApiService } from '../../../services/api.service';
import { CategoryService } from '../../../services/category.service';
import { ActorMessageService } from '../../../services/message.service';

// Import the shared GraphDataService
import { makeAgentNameUserFriendly } from '../../../lib/util';
import { GraphDataService } from '../../../services/graph-data.service';
import {
  Selectable,
  SelectionService,
} from '../../../services/selection.service';
import { HumanRequestComponent } from '../../human-request.component';

echarts.use([
  CanvasRenderer,
  TitleComponent,
  TooltipComponent,
  GraphChart,
  LegendComponent,
]);

@Component({
  selector: 'app-graph',
  imports: [
    CommonModule,
    FormsModule,
    DialogModule,
    ButtonModule,
    TabViewModule,
    TextareaModule,
    NgxEchartsDirective,
    HumanRequestComponent,
  ],
  templateUrl: './graph.component.html',
  styleUrls: ['./graph.component.scss'],
  providers: [provideEchartsCore({ echarts })],
})
export class GraphComponent {
  zone: NgZone = inject(NgZone);
  apiService: ApiService = inject(ApiService);
  akgentService: AkgentService = inject(AkgentService);
  categoryService: CategoryService = inject(CategoryService);
  messageService: ActorMessageService = inject(ActorMessageService);
  graphDataService: GraphDataService = inject(GraphDataService);
  selectionService: SelectionService = inject(SelectionService);

  echartsInstance: any;
  graphOptions: EChartsCoreOption = {};

  nodes: any[] = [];
  edges: any[] = [];
  categories: any[] = [];

  private chartCreated = false;

  private dataSub: Subscription = new Subscription();

  ngOnInit() {
    this.dataSub = combineLatest([
      this.graphDataService.nodes$,
      this.graphDataService.edges$,
      this.graphDataService.categories$,
    ]).subscribe(([updatedNodes, updatedEdges, updatedCats]) => {
      this.nodes = updatedNodes;
      this.edges = updatedEdges;
      this.categories = updatedCats;
      this.updateChart();
    });
  }

  ngOnDestroy() {
    this.dataSub.unsubscribe();
  }

  onChartInit(ec: any) {
    this.echartsInstance = ec;
    this.echartsInstance.resize();

    // Always create the chart when the instance is available, even with empty data
    if (!this.chartCreated) {
      this.createChart();
    }

    this.echartsInstance.on('click', (params: any) => {
      if (params.dataType === 'node') {
        const selectable: Selectable = {
          type: 'graph-node',
          data: params.data,
        };

        this.selectionService.handleSelection(selectable);
      } else if (params.dataType === 'edge') {
      }
    });

    this.echartsInstance.on('legendselectchanged', (params: any) => {
      const selectedCategories: boolean[] = this.categories.map(
        (cat) => !!params.selected[cat.name]
      );
      this.categoryService.setSelectedCategory(selectedCategories);
      this.zone.run(() => {});
    });
  }

  private createChart() {
    // Ensure arrays are initialized, use empty arrays as fallback
    const nodes = this.nodes || [];
    const edges = this.edges || [];
    const categories = this.categories || [];

    const labelFormatter = (params: any) => {
      const name = makeAgentNameUserFriendly(params.data.actorName);
      return params.data.humanRequests?.length ? `${name} 🙋 ` : name;
    };

    const chartOptions: EChartsCoreOption = {
      tooltip: {
        trigger: 'item',
        confine: true,
        extraCssText: 'max-width: 300px; white-space: normal; word-wrap: break-word;',
        formatter: (params: any) => {
          if (params.dataType === 'node' && params.data.errorMessage) {
            const name = makeAgentNameUserFriendly(params.data.actorName);
            const escaped = params.data.errorMessage
              .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            return `<b>${name}</b><br/><span style="color:darkred; font-size:11px">${escaped}</span>`;
          }
          return '';
        },
      },
      legend: [{ data: categories.map((c) => c.name), top: '8px' }],
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          force: {
            repulsion: 200,
            edgeLength: [50, 150],
          },
          label: {
            show: true,
            position: 'top',
            formatter: labelFormatter,
          },
          symbol: 'roundRect',
          symbolSize: [15, 15],
          edgeSymbol: ['none', 'arrow'],
          edgeSymbolSize: 10,
          lineStyle: {
            curveness: 0.1,
            width: 2,
            type: 'solid',
          },
          emphasis: {
            focus: 'adjacency',
            lineStyle: { width: 3 },
          },
          data: nodes,
          links: edges,
          categories: categories,
          zoom: 1.5,
        },
      ],
    };

    this.graphOptions = chartOptions;
    if (this.echartsInstance) {
      this.echartsInstance.setOption(chartOptions);
    }
    this.chartCreated = true;
  }

  private updateChart() {
    if (!this.echartsInstance) return;

    // Ensure arrays are initialized, use empty arrays as fallback
    const nodes = this.nodes || [];
    const edges = this.edges || [];
    const categories = this.categories || [];

    // Only update the data, not the entire chart configuration
    // This prevents the repositioning/sliding effect
    this.echartsInstance.setOption({
      legend: [{ data: categories.map((c) => c.name) }],
      series: [
        {
          data: nodes,
          links: edges,
          categories: categories,
        },
      ],
    });
  }
}
