/** Namensbereich „supplies“: Seite „Vorräte“ (pages/Supplies.tsx). Englisch = Quelle. */
const supplies = {
  checkedAgo: (ago: string) => `checked ${ago}`,
  sourceAuto: "automatic (supply low/out)",
  sourceMcp: "from the AI assistant (MCP)",
  notAdded: "Not added",
  notSaved: "Not saved",
  notDeleted: "Not deleted",
  title: "Supplies",
  subtitle: "Keep an eye on supplies, with the shopping list to go with them",
  shoppingList: "Shopping list",
  openCount: (n: number) => `${n} open`,
  showDone: "show completed",
  addPlaceholder: "Add item",
  add: "Add",
  listEmpty: "The list is empty.",
  reopen: "reopen",
  markDone: "done",
  remove: "Remove",
  autoOff: "Automatic entries are turned off (Settings → Sensors and supplies).",
  noSupplies: "No supplies yet",
  emptyBefore: "Under ",
  emptyLink: "Administration → Sensors",
  emptyAfter: " create a sensor marked as “Supply” – e.g. count bottles on a shelf, compare the fill level of a fruit bowl with “full/half/empty” reference images, or let the image model count small quantities (“How many eggs?”). Supplies running low then land on the shopping list automatically.",
  estimateNote: "The measurement is an estimate: hidden back rows, reflections and unknown packaging can distort the result. While a person is in the area, nothing is measured.",
};

export type SuppliesMessages = typeof supplies;
export default supplies;
