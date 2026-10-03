// Command go-app serves a small HTTP API.
package main

import (
	"log"
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/example/go-app/internal/handlers"
)

func main() {
	router := gin.Default()
	router.GET("/health", handlers.Health)
	router.GET("/users/:id", handlers.GetUser)

	log.Println("listening on :8080")
	if err := router.Run(":8080"); err != nil {
		log.Fatalf("server failed: %v", err)
	}
}
