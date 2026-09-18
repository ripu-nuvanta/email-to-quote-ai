import type { Quote, QuoteLine } from "./types";

export interface EmailTemplate {
  label: string;
  body: string;
}

function itemName(line: QuoteLine): string {
  return line.requested_text && !line.requested_text.startsWith("(") ? line.requested_text : line.description;
}

/** Ready-made customer replies built from the quote's own lines, for trying the follow-up flow quickly. */
export function followUpTemplates(quote: Quote): EmailTemplate[] {
  const first = quote.lines[0];
  const second = quote.lines[1] ?? quote.lines[0];
  const firstName = quote.customer?.name?.split(" ")[0] ?? "";
  const signOff = `\n\nThanks,\n${firstName}`.trimEnd();
  if (!first) {
    return [{ label: "Add an item", body: `Hi,\n\nCould you add 10 x hard hats (white) to the quote?${signOff}` }];
  }
  return [
    { label: "Change a quantity", body: `Hi,\n\nThanks for the quote. Could you change the ${itemName(first)} to ${Number(first.quantity) + 10}?${signOff}` },
    { label: "Remove an item", body: `Hi,\n\nPlease remove the ${itemName(second)} from the quote, we sourced those elsewhere.${signOff}` },
    { label: "Swap a product", body: `Hi,\n\nCould you swap the ${itemName(first)} for ${Number(first.quantity)} x hi vis vest XL?${signOff}` },
    { label: "Add an item", body: `Hi,\n\nPlease also add 5 sets drill bit set.${signOff}` },
    { label: "Just a thank-you", body: `Hi,\n\nThanks, this looks good. We'll send the PO early next week.${signOff}` },
  ];
}
