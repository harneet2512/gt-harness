package javaapp;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class OrderControllerTest {

    @Test
    public void greetsThroughService() {
        OrderService service = new OrderService();
        assertEquals("order ada", service.greet("ada"));
    }
}
