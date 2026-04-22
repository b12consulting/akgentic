import { SentMessage } from './message.types';

export interface ChatMessageInterface {
  name: string;
  actorName: string;
  humanRequests?: SentMessage[];
}

export interface NodeInterface {
  name: string;
  role: string;
  actorName: string;
  parentId: string;
  squadId: string;
  symbol: string;
  category: number;
  userMessage: boolean;
  itemStyle?: any;
  humanRequests?: SentMessage[];
  errorMessage?: string;
}

export interface EdgeInterface {
  source: string;
  target: string;
}
