package handlers

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

// GetUser returns a user by id.
func GetUser(c *gin.Context) {
	id := c.Param("id")
	if id == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "id is required"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"id": id, "name": "Ada"})
}

// Health reports service health.
func Health(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}
