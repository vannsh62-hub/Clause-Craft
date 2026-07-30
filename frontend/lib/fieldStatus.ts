export type FieldStatus = "known" | "new" | "required";

/** Derives a field's UI status from Variable Memory state: "known" if it existed before this
 *  fill operation opened, "new" if the user (or the assistant) supplied it just now, otherwise
 *  "required". */
export function fieldStatus(hasValue: boolean, wasKnown: boolean): FieldStatus {
  if (!hasValue) return "required";
  return wasKnown ? "known" : "new";
}
