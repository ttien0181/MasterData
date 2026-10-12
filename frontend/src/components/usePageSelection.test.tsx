import { act, renderHook } from '@testing-library/react';
import { expect, it } from 'vitest';
import { usePageSelection } from './usePageSelection';

it('adds another page without losing previous selections and removes page within all scope', () => {
  const { result } = renderHook(() => usePageSelection({}));
  act(() => result.current.selectPage(['a', 'b']));
  act(() => result.current.selectPage(['c']));
  expect(result.current.scope()).toEqual({ pair_ids: ['a','b','c'] });
  act(() => result.current.selectAll());
  act(() => result.current.toggle('c'));
  act(() => result.current.selectPage(['a','b']));
  expect(result.current.scope()).toEqual({ filter: {}, excluded_pair_ids: ['c'] });
});
