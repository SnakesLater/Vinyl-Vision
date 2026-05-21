# record-catalog-sam: Record Catalog Music App for Investor Demo
# Uses Discogs API for album metadata lookups
# Drop album art images to identify catalog entries

FROM node:20-alpine

WORKDIR /app

# Copy static assets first (for better Docker caching)
COPY index.html ./
COPY app.html ./

# Copy the express server (simple, no dependencies needed)
COPY server.js ./

# Copy package.json for npm install
COPY package.json ./

# Install Node.js dependencies
RUN npm install

# Expose the app port
EXPOSE 8081

# Default command
CMD ["node", "server.js"]
