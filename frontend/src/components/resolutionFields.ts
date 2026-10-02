import type { ResolutionItemDetail } from "@/lib/contracts";

type FieldOption = ResolutionItemDetail["field_options"][string][number];

export function groupFieldOptions(options: FieldOption[]) {
  const groups = new Map<string, FieldOption[]>();
  for (const option of options) groups.set(option.value, [...(groups.get(option.value) ?? []), option]);
  return [...groups].map(([value, sources]) => ({ value, sources }));
}

export function differenceSegments(value: string, otherValues: string[]) {
  const characters = Array.from(value);
  const shared = characters.map(() => true);
  for (const otherValue of otherValues) {
    if (otherValue === value) continue;
    const other = Array.from(otherValue);
    const lengths = Array.from({ length: characters.length + 1 }, () => Array<number>(other.length + 1).fill(0));
    for (let i = characters.length - 1; i >= 0; i--) {
      for (let j = other.length - 1; j >= 0; j--) {
        lengths[i][j] = characters[i] === other[j] ? 1 + lengths[i + 1][j + 1] : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
      }
    }
    const matched = characters.map(() => false);
    let i = 0, j = 0;
    while (i < characters.length && j < other.length) {
      if (characters[i] === other[j]) { matched[i] = true; i++; j++; }
      else if (lengths[i + 1][j] >= lengths[i][j + 1]) i++;
      else j++;
    }
    shared.forEach((_, index) => { shared[index] = shared[index] && matched[index]; });
  }
  const segments: { text: string; different: boolean }[] = [];
  characters.forEach((character, index) => {
    const different = !shared[index];
    if (segments.at(-1)?.different === different) segments[segments.length - 1].text += character;
    else segments.push({ text: character, different });
  });
  return segments;
}
