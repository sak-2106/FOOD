// FoodLens database setup (MongoDB)
//
// app.py creates these same collections and indexes automatically on
// startup (see init_db() in app.py), so you don't have to run this file
// manually. It's included for reference, or if you want to set the
// database up by hand:
//
//   mongosh "mongodb://localhost:27017/foodlens" init_db.js
//
// MongoDB is schemaless, so there are no CREATE TABLE statements — this
// just documents the two collections used and creates their indexes.
// Only two things are stored: user login credentials, and each scan's
// result output.

// One account per email.
db.users.createIndex({ email: 1 }, { unique: true });

// users document shape:
// {
//   _id:      ObjectId,
//   name:     String,
//   email:    String   (unique),
//   password: String   (hashed with werkzeug, never plain text)
// }

// Every history lookup filters by user_id and sorts newest first.
db.results.createIndex({ user_id: 1, created_at: -1 });

// results document shape:
// {
//   _id:         ObjectId,
//   user_id:     ObjectId,   (references users._id)
//   name:        String,     // product guess / label name shown in history
//   score:       Number | null,
//   scored:      Boolean,
//   score_label: String,
//   flags_count: Number,
//   thumb:       String,     // small base64 crop shown in the history list
//   data:        Object,     // the full analysis result (ingredients, nutrition, etc.)
//   created_at:  Date
// }
