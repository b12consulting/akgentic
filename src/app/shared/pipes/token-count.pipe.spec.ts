import { TokenCountPipe } from './token-count.pipe';

describe('TokenCountPipe (Epic 26 / ADR-022)', () => {
  let pipe: TokenCountPipe;

  beforeEach(() => {
    pipe = new TokenCountPipe();
  });

  it('renders sub-1000 values verbatim (passthrough)', () => {
    expect(pipe.transform(412)).toBe('412');
    expect(pipe.transform(0)).toBe('0');
    expect(pipe.transform(999)).toBe('999');
  });

  it('renders the k threshold with one decimal', () => {
    expect(pipe.transform(1000)).toBe('1.0k');
    expect(pipe.transform(12_031)).toBe('12.0k');
    expect(pipe.transform(1500)).toBe('1.5k');
  });

  it('renders the M threshold with one decimal', () => {
    expect(pipe.transform(1_000_000)).toBe('1.0M');
    expect(pipe.transform(1_200_000)).toBe('1.2M');
  });

  it('crosses k → M exactly at 1_000_000', () => {
    expect(pipe.transform(999_999)).toBe('1000.0k');
    expect(pipe.transform(1_000_000)).toBe('1.0M');
  });

  it('renders null / undefined / non-finite as "0"', () => {
    expect(pipe.transform(null)).toBe('0');
    expect(pipe.transform(undefined)).toBe('0');
    expect(pipe.transform(NaN)).toBe('0');
    expect(pipe.transform(Infinity)).toBe('0');
  });
});
